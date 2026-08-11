"""
Fuzzy label matching, numeric-token detection, and label<->value nearest-
match scoring. These functions work on tesseract's `image_to_data` output
(a dict of parallel lists: text/left/top/width/height/conf/line_num/...).

Row identity comes from the token BOUNDING BOXES, never from tesseract's
`line_num`. Both paths that feed this OCR with `--psm 11` (sparse text), under
which tesseract emits one *block* per token and restarts `line_num` inside each
one: measured over the fourteen fixtures and the captured ROI crops, every panel
came back with `block_num` running 1..N and `line_num` identically **1**. A
`line_num` comparison is therefore always True, which made the below/above/
unrelated branches of score_candidate unreachable and let a junk token 78 px
*below* a label score as though it sat beside it -- which is how `cash` was once
read off the fragment "6." at confidence 13 while the real $2,915.05 sat right
next to the CASH label. Do not reintroduce it.
"""
import math
import re
import difflib

from .config import NUMERIC_RE, COMPLETE_AMOUNT_RE, FUZZY_CUTOFF
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


# A number torn in two by OCR leaves its halves practically touching. The gap
# between them, in multiples of the narrower half's per-character width:
# measured 0.13 on "$2,190"|"90" (4 px against a 30.2 px advance) and 0.12 on
# "$2,18"|"6.20" (3 px against 26.0). The nearest pair anywhere in the corpus
# that is NOT a torn number is 1.55 -- two artwork glyph groups -- and the
# nearest genuine number-beside-number is 3.95, with two adjacent meter values
# at 6.93. So 0.6 sits 4.6x above the real fragments and 2.6x below the closest
# thing that could be mistaken for one.
FRAGMENT_GAP = 0.6

# Deliberately looser than SAME_ROW_OVERLAP, and NOT accompanied by a height
# check. Both were measured against the two real fragments and both would have
# thrown one of them away: "$2,190"|"90" has a height ratio of 1.98, because
# tesseract's box for the left half swallows the cell divider and runs 85 px
# against the right half's 43; and "$2,18"|"6.20" shares only 0.45 of the
# shorter box. The gap ratio above is what actually separates the two classes,
# by a factor of 12, so it is left to do the work alone.
FRAGMENT_ROW_OVERLAP = 0.3


def _glyph_advance(token):
    return token["width"] / max(1, len(token["text"]))


def _is_fragment_pair(a, b):
    """Are `a` and `b` two halves of one number that OCR split in the middle?

    `a` is the left half. Every token reaching here already contains a digit,
    which is what keeps a label->value gap out of consideration entirely.
    """
    gap = b["left"] - (a["left"] + a["width"])
    if gap < 0:
        return False                       # overlapping boxes are not a split
    if _vertical_overlap_fraction(a, b) < FRAGMENT_ROW_OVERLAP:
        return False
    if gap > FRAGMENT_GAP * max(1.0, min(_glyph_advance(a), _glyph_advance(b))):
        return False
    # Two well-formed amounts side by side are two meters, never one number.
    return not (COMPLETE_AMOUNT_RE.match(a["text"])
                and COMPLETE_AMOUNT_RE.match(b["text"]))


def _drop_fragment_pairs(tokens):
    """Discard BOTH halves of every number OCR tore in two.

    Both, not just the malformed one, and that is the whole point. On
    `2026-08-10_150454` a true $2,186.20 came back as "$2,18" + "6.20", and the
    right half is a perfectly well-formed amount: dropping only the left would
    leave "6.20" as the nearest complete number to the CASH label and report a
    balance of **6.20** -- a different wrong answer, and a more plausible-looking
    one than the 2.18 that shipped.

    Nothing is glued back together. Rebuilding "$2,190.90" out of "$2,190" and
    "90" means inventing the decimal point's position from a convention rather
    than recovering it from the image, and it fails silently and confidently if
    the engine dropped a digit along with the dot -- the same family as the
    "$2,202.155" -> 155 misread that GLUED_VALUE_RE's anchor exists to prevent.
    A null cash meter is caught downstream (validate.records.INFERABLE holds
    `win` alone, so a blank balance raises); a wrong one is not.
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

    Both rules are about what a misread looks like, not what a number looks
    like -- NUMERIC_RE has already decided the token is number-shaped.
    """
    # A meter never shows a negative. NUMERIC_RE and GLUED_VALUE_RE both allow
    # a leading sign for generality and nothing here has ever used it: zero
    # negative values across all 42 captured records and both fixture layouts.
    # What a sign actually marks is a shredded read. The WIN meter on
    # 2026-08-10_173258 shows $6.00 and, in the wider `bands` crop, comes back
    # as "-00" at confidence 83 -- and it only reaches the record at all
    # because rejecting the left-of-label pairing promoted it (WIN used to take
    # CASH's $2,185.10 and lose it again to the mutual-nearest check). Without
    # this rule that lands in the record as a confident -0.0.
    if "-" in text:
        return False

    # A token carrying a decimal point or a thousands separator is claiming to
    # be an amount, and an amount on these meters always ends in two decimals
    # (see COMPLETE_AMOUNT_RE). One that does not is a misread, not a number:
    # "6." is debris, "$2,190" and "$2,18" are the surviving halves of shredded
    # balances. A token with NO separator at all is left alone -- "90", "100",
    # "218295" may be a legitimate credit count, and
    # extract_field_value_prefer_currency already deprioritises bare integers
    # against currency-shaped values.
    return bool(COMPLETE_AMOUNT_RE.match(text)) or not any(ch in text for ch in ".,")


def find_text_tokens(data):
    """Return every token carrying a letter -- i.e. every title on the crop,
    whether or not it matched one of FIELD_LABELS.

    A meter title is always an English word, so anything lettery is somebody's
    title even when OCR mangled it past recognition. That matters for
    `_something_in_between`: on several frames BET arrives as "[B" + "ET" and
    never clears FUZZY_CUTOFF, and if only *recognised* titles could block, WIN
    would reach straight across that unread title and report BET's $1.00.
    Blocking on the raw lettery token costs nothing, because a title we cannot
    read is still proof that a different cell starts there.
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
        # not a clean number on its own — fall back to a value glued onto the end
        # of its LABEL, keeping just the numeric tail. The letters are what make
        # this safe: without them there is nothing to say the leading part is a
        # label rather than the significant digits of the number itself.
        m = GLUED_VALUE_RE.search(text)
        if m and any(ch.isalpha() for ch in text[:m.start()]):
            tokens.append(_token(data, i, m.group(1)))

    # Torn numbers go first, while both halves are still here to recognise each
    # other by. The rule below would otherwise remove the malformed half and
    # leave its sibling looking like a whole, innocent amount.
    tokens = _drop_fragment_pairs(tokens)

    return [t for t in tokens if _is_plausible_amount(t["text"])]


def looks_like_currency_value(text):
    """True for tokens that look like an actual meter amount ($482.50,
    1,250.00) rather than an incidental bare integer (a credit count,
    a denomination badge, etc.)."""
    return bool(re.search(r"[.,]", text)) or any(sym in text for sym in "$₹€£")


# ---------------------------------------------------------------------------
# Nearest-value matching (direction-aware distance heuristic)
# ---------------------------------------------------------------------------
# A value's box has to overlap its label's by this fraction of the SHORTER of
# the two heights before the pair counts as sharing a visual row. Measured over
# the fixtures and the captured crops, the split is total: genuine same-row
# pairs score 1.00, 1.00, 1.00, and a token on the row below scores 0.00. A
# *fraction* and not a pixel count is what survives _panel_ocr_scale's 3x/6x
# switch and the whole-image fallback's 2x.
SAME_ROW_OVERLAP = 0.5

# There is deliberately NO cap on how far to the right of its title a value may
# sit. **Empty space between a title and its value means nothing** -- a game is
# free to draw `CASH        $2,914.05` with half the bar between them -- so any
# distance cap is a guess about one layout's spacing that silently returns no
# value at all on a roomier one.
#
# This started as MAX_SAME_ROW_GAP = 4.0, the midpoint of a gap census taken on
# this cabinet (genuine pairs 0.35-2.62 of the taller box, cross-cell reaches
# 6.82-21.7). The census was real and the conclusion did not generalise: on a
# spacious layout a title and its own value measure 23.3 in those units, so the
# rule dropped the reading entirely. Raising the number only moves which layout
# it breaks. `_something_in_between` answers the actual question -- is there
# another title or another meter's money standing in the gap -- and distance was
# only ever a proxy for that.

# Distance multiplier for a value STACKED against its label -- directly above or
# directly below it. One constant for both directions, deliberately: a value is
# drawn to the right of its label, or above it, or below it, and never to the
# left, and those three placements are equally valid. Which one a game uses is a
# fact about that game, not a probability to be encoded here.
STACKED_PENALTY = 0.65


def _vertical_overlap_fraction(a, b):
    """How much of the shorter box's height the two boxes share, 0.0-1.0."""
    top = max(a["top"], b["top"])
    bottom = min(a["top"] + a["height"], b["top"] + b["height"])
    return max(0, bottom - top) / max(1, min(a["height"], b["height"]))


def on_same_visual_row(label, candidate):
    """Are these two tokens drawn on the same line of the meter bar?

    This is what `line_num` was doing before it was measured to be identically
    1 on every token (see the module docstring).
    """
    return _vertical_overlap_fraction(label, candidate) >= SAME_ROW_OVERLAP


def _something_in_between(label, candidate, blockers):
    """Does another title or another number sit in the gap between them?

    This is what actually keeps a label off the next cell's money, and it
    replaces asking how far apart they are. On a meter bar the cells are
    `TITLE value | TITLE value | TITLE value`, so the thing that makes
    `WIN -> $1.00` wrong is not the distance -- it is the BET title standing in
    between. Distance only ever stood in as a proxy for that, and it is a bad
    proxy: whitespace after a title carries no meaning, so a game that spaces
    its meters out generously was being read as though every value belonged to
    a different cell.

    Only recognised titles and recognised numbers count as blockers, which is
    exactly the vocabulary of these bars -- the title is always an English word
    and the value is always digits, optionally with a currency symbol. Cell
    dividers, bracket ticks and background artwork are not in `blockers` and so
    cannot wall a value off from its own title.
    """
    gap_start = label["left"] + label["width"]
    gap_end = candidate["left"]
    for other in blockers:
        if other is label or other is candidate:
            continue
        if not on_same_visual_row(label, other):
            continue
        # Its midpoint has to be inside the gap: a token that merely grazes the
        # edge is one whose box bled into its neighbour, not a separate cell.
        mid = other["left"] + other["width"] / 2
        if gap_start < mid < gap_end:
            return True
    return False


def value_belongs_to_another_cell(label, candidate, blockers=()):
    """True when `candidate` cannot be `label`'s value, on the geometry alone.

    The cabinet's meter bar is a row of `LABEL value` cells --
    `CASH $2,915.05 | WIN $0.30 | BET $1.00` -- and the operator's rule is that
    **a value is never drawn to the left of its own label**. It is beside the
    label in reading order, or stacked above or below it, never behind it.
    (Right, above and below are all real: another game draws the value above
    its title, which is why score_candidate charges the two stacked directions
    the same.) That is a
    hard fact about the layout rather than a preference, so it is enforced as a
    rejection; scoring it as merely unlikely was measured to lose. On
    `2026-08-10_173530` the cash amount shredded into "$2,190" + "90" and the
    orphan "90", sitting entirely to the LEFT of the WIN label, was awarded to
    WIN anyway -- 114.2 px x 1.1 = 125.7 against CASH's 317.7 x 0.5 = 158.9.
    No penalty short of rejection changes that outcome.

    `blockers` is every recognised title and number on the crop; it is what the
    between-rule reads, and passing nothing simply skips that rule. All three
    rules only mean anything along the label's own row -- a value stacked under
    a label is left to the below/above ladder in score_candidate.
    """
    if not on_same_visual_row(label, candidate):
        return False

    # Edges, not centres. A value box is routinely 2-5x wider than its label
    # ("$2,190" is 181 px against "CASH"'s 96), so an amount that genuinely
    # *starts* to the right of its label can still have its centre to the left
    # of the label's centre -- rejecting on `dx` would throw that pairing away.
    # Edges answer the question actually being asked: is it entirely behind?
    if candidate["left"] + candidate["width"] <= label["left"]:
        return True

    # Something else's title or money standing in the gap. Distance is not
    # consulted at all -- see the note above MAX_SAME_ROW_GAP's removal.
    return _something_in_between(label, candidate, blockers)


def score_candidate(label, candidate, img_w, img_h, blockers=()):
    """Cost of reading `candidate` as `label`'s value. Lower is better.

    `blockers` is every other recognised title and number on the crop, used to
    tell "this value is across the bar from its title" apart from "this game
    just leaves a lot of space after the title". It defaults to empty so the
    signature stays usable without it.

    Returns `math.inf` for a pairing the geometry rules out. `inf` rather than
    `None` because every caller already handles it untouched: both
    extract_field_value and extract_field_value_prefer_currency drop anything
    over `max_allowed`, and `inf * 2.2` is `inf` and not NaN. `None` would need
    a branch at all three call sites and would raise inside the `min(key=...)`
    in extraction.extract_fields_via_panel_word_ocr.
    """
    dx = candidate["cx"] - label["cx"]
    dy = candidate["cy"] - label["cy"]
    dist = (dx ** 2 + dy ** 2) ** 0.5

    if value_belongs_to_another_cell(label, candidate, blockers):
        return math.inf

    if on_same_visual_row(label, candidate):
        # 1.1 is now only reachable when the boxes overlap horizontally while
        # the value's centre sits left of the label's -- a value wider than its
        # own cell. A weak reading, not an impossible one, so it survives.
        return dist * (0.5 if dx >= 0 else 1.1)

    # Below/above/unrelated -- a value STACKED with its label rather than beside
    # it. These three branches were dead code for as long as the row test was
    # `line_num` (always equal, so always the same-line branch), which means
    # every correct reading in the corpus was produced without them ever
    # running. Switching them on for the first time has to be done carefully,
    # and measurement said it needs both of the guards below.
    #
    # Guard 1: a stacked value must be currency-shaped. Turning these branches
    # on unguarded invented `win = 200.0` on three of the fourteen fixtures --
    # the bet-level buttons (100/200/300/500/800), sitting 9 label-heights below
    # the WIN label in a full-screen `dynamic` crop. A bare integer that is not
    # even on its label's row is a decoy every time; the same integer ON the row
    # (a CREDITS meter showing a whole count) is untouched by this, and so is
    # every currency-shaped stacked value. Bounding the vertical distance
    # instead does NOT work: one of those pairings measured a gap of 0.03 label
    # heights, because tesseract's box for that `win` swallowed the panel
    # divider and came back 173 px tall against the value's 56.
    if not looks_like_currency_value(candidate["text"]):
        return math.inf

    # Guard 2: the boxes must actually share columns, ANDed with the original
    # quarter-of-the-panel bound. `abs(dx) < img_w * 0.25` alone spans a whole
    # neighbouring meter cell, and that looseness is what let the junk token
    # "6." -- 10 px past CASH's right edge, sharing no column with it -- be read
    # as CASH's value at confidence 13.
    horiz_overlap = (
        abs(dx) < img_w * 0.25
        and min(candidate["left"] + candidate["width"], label["left"] + label["width"])
        > max(candidate["left"], label["left"])
    )
    if horiz_overlap:
        # Directly above or directly below, and the two score the SAME. The
        # layout rule is that a value sits to the right of its label or stacked
        # against it, above or below, and never to the left -- all three
        # positions are equally legitimate, so the multiplier must not rank
        # them. An earlier version charged above 0.75 against below's 0.65 on
        # the assumption that below was the commoner stacking; there is a game
        # that draws the value ABOVE its title, and on it that guess is simply
        # a thumb on the scale against the correct reading.
        #
        # Same-row-to-the-right keeps its slightly cheaper 0.5, which is not a
        # statement about legality: on a single-line meter bar it is the more
        # specific reading, and in a stacked layout there is no same-row
        # candidate for it to outrank anyway.
        return dist * STACKED_PENALTY
    return dist * 1.6          # unrelated area — discouraged


def extract_field_value(label_matches, numeric_tokens, img_w, img_h, blockers=()):
    """Given all label-token candidates for one field and all numeric tokens
    on the image, pick the best (label, value) pairing. Used by the
    whole-image last-resort fallback method only."""
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
    """Find the best matching numeric token for a SINGLE label occurrence,
    strongly preferring tokens that look like currency amounts ($482.50,
    1,250.00) over bare integers such as a small credit-equivalent count
    sitting next to the real value.

    `blockers` should be the other TITLES found on the crop; the numeric tokens
    below are added to it, so a value with another meter's money between it and
    this label is ruled out too.
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
