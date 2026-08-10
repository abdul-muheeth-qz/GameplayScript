"""
Fuzzy label matching, numeric-token detection, and label<->value nearest-
match scoring. These functions work on tesseract's `image_to_data` output
(a dict of parallel lists: text/left/top/width/height/conf/line_num/...).
"""
import re
import difflib

from .config import NUMERIC_RE, FUZZY_CUTOFF
from .ocr_utils import token_conf


def clean_token(text):
    return re.sub(r"[^A-Z0-9]", "", text.upper())


# A real UI label is never drawn with digits in it, so a digit surviving in an
# otherwise-lettery OCR token is almost certainly a misread of a similar-shaped
# letter -- not a signal that the token isn't a label at all. On this cabinet's
# bold condensed font, tesseract reads the BET label as "8ET" at every scale
# and threshold tried (psm 3/6/7/11, both binarization polarities), which
# otherwise permanently loses BET's value: "8ET" is 3 characters, short of the
# 4-character floor label_similarity requires before trusting the fuzzy ratio,
# so it never gets a chance to score against "BET" at all.
_LETTER_LOOKALIKES = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "6": "G"})


def _delookalike(cleaned):
    """`cleaned` with digit/letter lookalikes swapped back to letters -- but
    only when it already contains a real letter, so a genuinely numeric OCR
    token (a meter value, not a label) is never reinterpreted as one."""
    if cleaned.isalpha() or cleaned.isdigit():
        return cleaned
    return cleaned.translate(_LETTER_LOOKALIKES)


def label_similarity(token, label_variants):
    cleaned = clean_token(token)
    if not cleaned:
        return 0.0
    candidates = {cleaned, _delookalike(cleaned)}
    best = 0.0
    for cleaned in candidates:
        for variant in label_variants:
            # exact match scores highest
            if cleaned == variant:
                return 1.0
            # a known label word fully appearing inside noisier OCR text is a
            # strong, safe signal (e.g. cleaned="XCASHX" contains "CASH").
            if variant in cleaned and len(variant) >= 3:
                best = max(best, 0.95)
            # the reverse (OCR fragment contained inside a longer label word) is
            # only trustworthy if the fragment itself is a substantial chunk of
            # that word — short fragments like "TO" trivially sit inside several
            # different label words (TOTALBET, TOTALWIN), and even 3-letter
            # fragments can coincidentally be the tail of an unrelated word
            # (e.g. "INS" is the last 3 letters of "WINS") — so require a
            # slightly longer, more specific overlap here.
            elif cleaned in variant and len(cleaned) >= max(4, len(variant) * 0.7):
                best = max(best, 0.85)
            # the plain fuzzy ratio is only trustworthy once the OCR fragment
            # has enough characters — very short fragments (3 chars or fewer)
            # get inflated similarity scores almost by chance (e.g. "INS"
            # happens to be the exact tail of "WINS"), so skip the ratio
            # fallback for them; exact matches above already handle genuinely
            # short real labels like "BET"/"WIN".
            if len(cleaned) >= 4:
                ratio = difflib.SequenceMatcher(None, cleaned, variant).ratio()
                best = max(best, ratio)
    return best


# Trailing number in a word, for values glued to their label by OCR
# ("BALANCE1,250.00"). A match always contains a digit — the `[\d]` in the
# middle is required — so callers need no separate has-a-digit check.
GLUED_VALUE_RE = re.compile(r"([\$₹€£]?-?[\d][\d,]*\.?\d{0,2})$")

# A well-formed amount with extra digits stuck on the end: "$2,202.155".
# Currency has two decimal places, so anything past the second is something
# else that OCR ran into the number — on this cabinet it is the orange bracket
# tick drawn after each meter, which reads as a 5 about half the time.
#
# This has to be tried BEFORE the glued rule, and the glued rule has to refuse
# a token with no letters in front of the number, or the two disagree in the
# worst possible way: GLUED_VALUE_RE anchors at the END, so on "$2,202.155" it
# happily returns "155" — a confident, plausible, entirely invented number that
# a spin was once judged against.
OVERPRECISE_RE = re.compile(r"^([\$₹€£]?\s?-?[\d,]+\.\d{2})\d+$")


def _token(data, i, text, **extra):
    """Build a token record from row `i` of an image_to_data dict.

    `text` is passed in rather than read back out of the row because
    find_numeric_tokens sometimes keeps only the numeric tail of a word;
    the bbox stays that of the whole word either way.
    """
    x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
    return {
        "text": text,
        "line_num": data["line_num"][i],
        "left": x, "top": y, "width": w, "height": h,
        "cx": x + w / 2, "cy": y + h / 2,
        "conf": token_conf(data["conf"][i]),
        **extra,
    }


def find_label_tokens(data, label_variants):
    """Return token dicts (bbox/center/conf, plus a "sim" score) whose text
    fuzzy-matches any of the given label variants."""
    matches = []
    for i, raw in enumerate(data["text"]):
        text = raw.strip()
        if not text:
            continue
        sim = label_similarity(text, label_variants)
        if sim >= FUZZY_CUTOFF:
            matches.append(_token(data, i, text, sim=sim))
    return matches


def find_numeric_tokens(data):
    """Return token dicts whose text looks like a numeric meter value."""
    tokens = []
    for i, raw in enumerate(data["text"]):
        text = raw.strip()
        if not text:
            continue
        if NUMERIC_RE.match(text) and any(ch.isdigit() for ch in text):
            tokens.append(_token(data, i, text))
            continue
        # An amount with junk digits on the end: keep the amount, drop the junk.
        m = OVERPRECISE_RE.match(text)
        if m:
            tokens.append(_token(data, i, m.group(1)))
            continue
        # not a clean number on its own — fall back to a value glued onto the end
        # of its LABEL, keeping just the numeric tail. The letters are what make
        # this safe: without them there is nothing to say the leading part is a
        # label rather than the significant digits of the number itself.
        m = GLUED_VALUE_RE.search(text)
        if m and any(ch.isalpha() for ch in text[:m.start()]):
            tokens.append(_token(data, i, m.group(1)))
    return tokens


def looks_like_currency_value(text):
    """True for tokens that look like an actual meter amount ($482.50,
    1,250.00) rather than an incidental bare integer (a credit count,
    a denomination badge, etc.)."""
    return bool(re.search(r"[.,]", text)) or any(sym in text for sym in "$₹€£")


# ---------------------------------------------------------------------------
# Nearest-value matching (direction-aware distance heuristic)
# ---------------------------------------------------------------------------
def score_candidate(label, candidate, img_w, img_h):
    dx = candidate["cx"] - label["cx"]
    dy = candidate["cy"] - label["cy"]
    dist = (dx ** 2 + dy ** 2) ** 0.5

    same_line = candidate["line_num"] == label["line_num"]
    horiz_overlap = abs(dx) < img_w * 0.25

    if same_line and dx >= 0:
        penalty = 0.5          # value beside label, in reading order
    elif same_line:
        # Value to the LEFT of the label, on the same line. Plain distance is
        # undirected, so on a single-line meter bar reading
        # "CASH $2,208.35  WIN  BET $1.00" the cash value sits almost exactly
        # between CASH and WIN -- 214 px from one, 206 px from the other -- and
        # the nearer label wins by 8 px, handing cash's money to WIN. A label
        # is written before its value, so a value found behind one is the
        # weaker reading. Still cheaper than the 1.6 below, so a genuinely
        # right-aligned layout can outrank an unrelated diagonal match.
        penalty = 1.1
    elif dy > 0 and horiz_overlap:
        penalty = 0.65         # value directly below label (very common)
    elif dy < 0 and horiz_overlap:
        penalty = 0.75         # value directly above label
    else:
        penalty = 1.6          # unrelated area — discouraged

    return dist * penalty


def extract_field_value(label_matches, numeric_tokens, img_w, img_h):
    """Given all label-token candidates for one field and all numeric tokens
    on the image, pick the best (label, value) pairing. Used by the
    whole-image last-resort fallback method only."""
    best = None
    max_allowed = ((img_w ** 2 + img_h ** 2) ** 0.5) * 0.35
    for label in label_matches:
        for cand in numeric_tokens:
            s = score_candidate(label, cand, img_w, img_h)
            if s > max_allowed:
                continue
            if best is None or s < best["score"]:
                best = {"score": s, "label": label, "value_token": cand}
    return best


def extract_field_value_prefer_currency(label, numeric_tokens, img_w, img_h):
    """Find the best matching numeric token for a SINGLE label occurrence,
    strongly preferring tokens that look like currency amounts ($482.50,
    1,250.00) over bare integers such as a small credit-equivalent count
    sitting next to the real value."""
    best = None
    max_allowed = ((img_w ** 2 + img_h ** 2) ** 0.5) * 0.45
    for cand in numeric_tokens:
        s = score_candidate(label, cand, img_w, img_h)
        if not looks_like_currency_value(cand["text"]):
            s *= 2.2  # de-prioritize bare integers
        if s > max_allowed:
            continue
        if best is None or s < best["score"]:
            best = {"score": s, "value_token": cand}
    return best


def clean_numeric_value(raw):
    """Parse an OCR'd number into a float, robust to a couple of common
    OCR mistakes on currency-formatted text:
      - a comma thousands-separator misread as a period, producing two
        decimal points (e.g. "$1,175.76" -> "1.175.76")
      - a stray currency symbol or other junk character
    """
    s = re.sub(r"[^\d.\-,]", "", raw)
    if not s:
        return None

    if s.count(".") > 1:
        # multiple dots: OCR likely misread a thousands-separator comma as
        # a period. Treat every dot except the last as a thousands
        # separator, and the last as the real decimal point. Any surviving
        # commas are thousands separators too, by definition, so drop them
        # as well — leaving them in would make float() raise below.
        head, _, tail = s.rpartition(".")
        s = head.replace(".", "").replace(",", "") + "." + tail
    elif "," in s and "." in s:
        # normal case: comma = thousands separator, dot = decimal point
        s = s.replace(",", "")
    elif "," in s:
        # only commas, no dot: distinguish thousands separators ("1,175",
        # "12,345,678") from a European-style decimal comma like "20,00".
        # Note the repeated group and the optional sign — without them a
        # value of a million or more, or a negative one, fell through to the
        # decimal-comma branch and became unparseable (or silently wrong).
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")

    if s in ("", "-", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None
