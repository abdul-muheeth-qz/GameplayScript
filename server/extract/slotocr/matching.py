"""Fuzzy label matching, numeric-token detection, and label<->value scoring over tesseract's
`image_to_data` output.

Row identity comes from the token BOUNDING BOXES, never from tesseract's `line_num`: under
`--psm 11` it comes back identically 1 on every token, which made the stacked branches of
`score_candidate` dead code and let a token 78 px below a label score as though it sat beside it.
Do not reintroduce it.
"""
import math
import re
import difflib

from .config import NUMERIC_RE, COMPLETE_AMOUNT_RE, FUZZY_CUTOFF
from .ocr_utils import token_conf


def clean_token(text):
    return re.sub(r"[^A-Z0-9]", "", text.upper())


# A real UI label is never drawn with digits, so a digit in an otherwise-lettery token is a
# misread of a similar-shaped letter. This cabinet reads BET as "8ET" at every scale and
# threshold tried, and "8ET" is too short for label_similarity's 4-character fuzzy floor -- so
# without this, BET's value is permanently lost.
_LETTER_LOOKALIKES = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "6": "G"})


def _delookalike(cleaned):
    """`cleaned` with digit/letter lookalikes swapped back to letters, but only when it already
    holds a real letter -- so a genuinely numeric token is never reinterpreted as a label."""
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
            if cleaned == variant:
                return 1.0
            # A whole label word inside noisier OCR text is a strong, safe signal.
            if variant in cleaned and len(variant) >= 3:
                best = max(best, 0.95)
            # The reverse only holds for a substantial chunk: short fragments sit inside several
            # label words ("TO" in TOTALBET/TOTALWIN, "INS" is the tail of "WINS").
            elif cleaned in variant and len(cleaned) >= max(4, len(variant) * 0.7):
                best = max(best, 0.85)
            # The plain ratio inflates for fragments of 3 characters or fewer; exact matches above
            # already cover genuinely short labels like BET and WIN.
            if len(cleaned) >= 4:
                ratio = difflib.SequenceMatcher(None, cleaned, variant).ratio()
                best = max(best, ratio)
    return best


# Trailing number in a word, for a value glued to its label ("BALANCE1,250.00"). A match always
# contains a digit, so callers need no separate check.
GLUED_VALUE_RE = re.compile(r"([\$₹€£]?-?[\d][\d,]*\.?\d{0,2})$")

# A well-formed amount with junk digits stuck on the end ("$2,202.155" -- the orange bracket tick
# after each meter, which reads as a 5 about half the time). Must be tried BEFORE the glued rule,
# which anchors at the END and would happily return "155" as the value.
OVERPRECISE_RE = re.compile(r"^([\$₹€£]?\s?-?[\d,]+\.\d{2})\d+$")


def _token(data, i, text, **extra):
    """A token record from row `i`. `text` is passed in because find_numeric_tokens sometimes
    keeps only the numeric tail of a word; the bbox stays that of the whole word."""
    x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
    return {
        "text": text,
        "left": x, "top": y, "width": w, "height": h,
        "cx": x + w / 2, "cy": y + h / 2,
        "conf": token_conf(data["conf"][i]),
        **extra,
    }


def find_label_tokens(data, label_variants):
    """Token dicts (bbox/centre/conf, plus a "sim" score) whose text fuzzy-matches a variant."""
    matches = []
    for i, raw in enumerate(data["text"]):
        text = raw.strip()
        if not text:
            continue
        sim = label_similarity(text, label_variants)
        if sim >= FUZZY_CUTOFF:
            matches.append(_token(data, i, text, sim=sim))
    return matches


# The gap between two halves of a torn number, in multiples of the narrower half's per-character
# width. The two real fragments measure 0.13 and 0.12; the nearest non-fragment pair in the corpus
# is 1.55, the nearest genuine number-beside-number 3.95, two adjacent meter values 6.93.
FRAGMENT_GAP = 0.6

# Deliberately looser than SAME_ROW_OVERLAP, and with no height check beside it: both would have
# thrown away a real fragment ("$2,190"|"90" has a height ratio of 1.98 because the left box
# swallowed the cell divider; "$2,18"|"6.20" shares only 0.45 of the shorter box).
FRAGMENT_ROW_OVERLAP = 0.3


def _glyph_advance(token):
    return token["width"] / max(1, len(token["text"]))


def _is_fragment_pair(a, b):
    """Are `a` (the left half) and `b` two halves of one number OCR split in the middle?"""
    gap = b["left"] - (a["left"] + a["width"])
    if gap < 0:
        return False                       # overlapping boxes are not a split
    if _vertical_overlap_fraction(a, b) < FRAGMENT_ROW_OVERLAP:
        return False
    if gap > FRAGMENT_GAP * max(1.0, min(_glyph_advance(a), _glyph_advance(b))):
        return False
    # The LEFT half has to be incomplete, and that alone decides it: a torn number always leaves
    # its left part missing the cents, so an `a` that carries them is finished and whatever
    # follows belongs to something else. "At least one of the two is incomplete" -- which this
    # started as -- ate a good "$1.00" next to a stray artwork "2" and emptied the BET meter.
    return not COMPLETE_AMOUNT_RE.match(a["text"])


def _drop_fragment_pairs(tokens):
    """Discard BOTH halves of every number OCR tore in two.

    Both, because the right half is often well-formed on its own: dropping only "$2,18" leaves
    "6.20" as the nearest complete number to CASH and reports a balance of 6.20. Nothing is glued
    back together either -- that means inventing the decimal point's position from a convention.
    """
    ordered = sorted(tokens, key=lambda t: t["left"])
    doomed = set()
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if _is_fragment_pair(a, b):
                doomed.add(id(a))
                doomed.add(id(b))
    return [t for t in tokens if id(t) not in doomed]


def _is_plausible_amount(text):
    """Could `text` be something one of these meters actually shows?

    Both rules are about what a misread looks like; NUMERIC_RE has already decided the token is
    number-shaped.
    """
    # A meter never shows a negative -- zero across all 42 captured records and both fixture
    # layouts. What a sign actually marks is a shredded read: a $6.00 WIN came back as "-00" at
    # confidence 83, which without this lands in the record as a confident -0.0.
    if "-" in text:
        return False

    # A token carrying a separator is claiming to be an amount, and an amount here always ends in
    # two decimals. One that does not is a misread ("6." is debris, "$2,190" a shredded balance).
    # A token with NO separator is left alone: "90", "218295" may be a legitimate credit count,
    # and bare integers are already deprioritised against currency-shaped values.
    return bool(COMPLETE_AMOUNT_RE.match(text)) or not any(ch in text for ch in ".,")


def find_text_tokens(data):
    """Every token carrying a letter -- i.e. every title, whether or not it matched FIELD_LABELS.

    A meter title is always an English word, so anything lettery is somebody's title even when OCR
    mangled it past recognition. BET arrives as "[B" + "ET" on several frames, and if only
    *recognised* titles could block, WIN would reach across it and report BET's $1.00.
    """
    return [_token(data, i, t.strip())
            for i, t in enumerate(data["text"])
            if t.strip() and any(ch.isalpha() for ch in t)]


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
        # Otherwise a value glued onto the end of its LABEL, keeping the numeric tail. The letters
        # are what make it safe: without them nothing says the leading part is a label rather than
        # the significant digits of the value.
        m = GLUED_VALUE_RE.search(text)
        if m and any(ch.isalpha() for ch in text[:m.start()]):
            tokens.append(_token(data, i, m.group(1)))

    # Torn numbers first, while both halves are still here to recognise each other by -- the rule
    # below would otherwise remove the malformed half and leave its sibling looking whole.
    tokens = _drop_fragment_pairs(tokens)

    return [t for t in tokens if _is_plausible_amount(t["text"])]


def looks_like_currency_value(text):
    """True for a real meter amount ($482.50, 1,250.00) rather than an incidental bare integer."""
    return bool(re.search(r"[.,]", text)) or any(sym in text for sym in "$₹€£")


# How much of the shorter box's height a value and its label must share to count as one visual
# row. Measured, the split is total: genuine same-row pairs score 1.00, a token on the row below
# 0.00. A fraction rather than a pixel count survives the 3x/6x scale switch.
SAME_ROW_OVERLAP = 0.5

# There is deliberately NO cap on how far right of its title a value may sit: empty space means
# nothing, and a game may draw `CASH        $2,914.05`. This was MAX_SAME_ROW_GAP = 4.0, measured
# accurately on this cabinet and wrong on a roomier layout where a title and its own value measure
# 23.3. `_something_in_between` answers the real question; distance was only ever a proxy for it.

# For a value STACKED against its label, above or below. One constant for both directions
# deliberately: right, above and below are all legitimate, so the multiplier must not rank them.
STACKED_PENALTY = 0.65


def _vertical_overlap_fraction(a, b):
    """How much of the shorter box's height the two boxes share, 0.0-1.0."""
    top = max(a["top"], b["top"])
    bottom = min(a["top"] + a["height"], b["top"] + b["height"])
    return max(0, bottom - top) / max(1, min(a["height"], b["height"]))


def on_same_visual_row(label, candidate):
    """Are these two tokens drawn on the same line of the meter bar?"""
    return _vertical_overlap_fraction(label, candidate) >= SAME_ROW_OVERLAP


def _something_in_between(label, candidate, blockers):
    """Does another title or another meter's money sit in the gap between them?

    This is what keeps a label off the next cell's money, and it replaces asking how far apart
    they are. Dividers, bracket ticks and artwork are not blockers and cannot wall a value off
    from its own title.
    """
    gap_start = label["left"] + label["width"]
    gap_end = candidate["left"]
    for other in blockers:
        if other is label or other is candidate:
            continue
        if not on_same_visual_row(label, other):
            continue
        # The midpoint has to be inside the gap: a token grazing the edge is one whose box bled
        # into its neighbour, not a separate cell.
        mid = other["left"] + other["width"] / 2
        if gap_start < mid < gap_end:
            return True
    return False


def value_belongs_to_another_cell(label, candidate, blockers=()):
    """True when `candidate` cannot be `label`'s value, on the geometry alone.

    The bar is a row of `LABEL value` cells and a value is never drawn to the LEFT of its own
    label. That is a hard fact about the layout, so it is a rejection rather than a penalty:
    scoring it as merely unlikely awarded an orphan "90" left of the WIN label to WIN anyway, at
    125.7 against CASH's 158.9, and no penalty short of rejection changes that.
    """
    if not on_same_visual_row(label, candidate):
        return False

    # Edges, not centres. A value box is routinely 2-5x wider than its label, so an amount that
    # genuinely *starts* right of its label can still have its centre to the left.
    if candidate["left"] + candidate["width"] <= label["left"]:
        return True

    return _something_in_between(label, candidate, blockers)


def score_candidate(label, candidate, img_w, img_h, blockers=()):
    """Cost of reading `candidate` as `label`'s value. Lower is better.

    `math.inf` for a pairing the geometry rules out -- inf rather than None because every caller
    already handles it untouched (`inf * 2.2` is inf, and None would raise inside a `min(key=)`).
    """
    dx = candidate["cx"] - label["cx"]
    dy = candidate["cy"] - label["cy"]
    dist = (dx ** 2 + dy ** 2) ** 0.5

    if value_belongs_to_another_cell(label, candidate, blockers):
        return math.inf

    if on_same_visual_row(label, candidate):
        # 1.1 is only reachable when the boxes overlap horizontally while the value's centre sits
        # left of the label's -- a value wider than its own cell. Weak, not impossible.
        return dist * (0.5 if dx >= 0 else 1.1)

    # A value stacked with its label rather than beside it. These branches were dead for as long
    # as the row test was `line_num`, so switching them on is new behaviour, and it needs both
    # guards below.
    #
    # Guard 1: a stacked value must be currency-shaped. Unguarded, this invented `win = 200.0` on
    # three fixtures off the bet-level buttons nine label-heights below the WIN label. Bounding
    # the vertical distance instead does not work -- one such pairing measures 0.03 label heights,
    # because tesseract's box for that `win` swallowed the divider and came back 173 px tall.
    if not looks_like_currency_value(candidate["text"]):
        return math.inf

    # Guard 2: the boxes must share columns, ANDed with the quarter-of-the-panel bound. The bound
    # alone spans a whole neighbouring cell, which is how the junk token "6." was read as CASH's
    # value at confidence 13.
    horiz_overlap = (
        abs(dx) < img_w * 0.25
        and min(candidate["left"] + candidate["width"], label["left"] + label["width"])
        > max(candidate["left"], label["left"])
    )
    if horiz_overlap:
        return dist * STACKED_PENALTY
    return dist * 1.6          # unrelated area — discouraged


def extract_field_value(label_matches, numeric_tokens, img_w, img_h, blockers=()):
    """The best (label, value) pairing for one field. The whole-image fallback method only."""
    best = None
    blockers = list(blockers) + list(numeric_tokens)
    max_allowed = ((img_w ** 2 + img_h ** 2) ** 0.5) * 0.35
    for label in label_matches:
        for cand in numeric_tokens:
            s = score_candidate(label, cand, img_w, img_h, blockers)
            if s > max_allowed:
                continue
            if best is None or s < best["score"]:
                best = {"score": s, "label": label, "value_token": cand}
    return best


def extract_field_value_prefer_currency(label, numeric_tokens, img_w, img_h,
                                        blockers=()):
    """The best numeric token for a SINGLE label occurrence, preferring currency-shaped amounts
    over bare integers such as a credit count beside the real value.

    `blockers` should be the other TITLES on the crop; the numeric tokens are added to it, so a
    value with another meter's money between it and this label is ruled out too.
    """
    best = None
    blockers = list(blockers) + list(numeric_tokens)
    max_allowed = ((img_w ** 2 + img_h ** 2) ** 0.5) * 0.45
    for cand in numeric_tokens:
        s = score_candidate(label, cand, img_w, img_h, blockers)
        if not looks_like_currency_value(cand["text"]):
            s *= 2.2  # de-prioritize bare integers
        if s > max_allowed:
            continue
        if best is None or s < best["score"]:
            best = {"score": s, "value_token": cand}
    return best


def clean_numeric_value(raw):
    """An OCR'd number as a float, robust to a thousands-comma misread as a period and to stray
    currency symbols."""
    s = re.sub(r"[^\d.\-,]", "", raw)
    if not s:
        return None

    if s.count(".") > 1:
        # Every dot but the last is a thousands separator misread as a period. Surviving commas
        # are separators too by definition, and leaving them in would make float() raise.
        head, _, tail = s.rpartition(".")
        s = head.replace(".", "").replace(",", "") + "." + tail
    elif "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:
        # Only commas: thousands separators ("1,175") against a European decimal comma ("20,00").
        # The repeated group and the optional sign are load-bearing -- without them a value of a
        # million or more, or a negative one, fell through and became unparseable.
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
