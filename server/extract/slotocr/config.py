"""
Shared configuration/constants for the slot-meter OCR pipeline.
"""
import re

# Label synonyms for each logical meter field we want to extract.
#
# The keys are the field names that come out the other end, and they are what
# the validate stage reads (validate/agent.py's FIELDS). The cash meter's key is
# "cash" rather than "balance" for exactly that reason -- the two halves used to
# disagree, and a rename here is the single point of change, because everything
# downstream iterates this dict. The synonyms are what Tesseract might have read
# off the screen, so BALANCE stays in the list.
FIELD_LABELS = {
    "cash": ["BALANCE", "CASH", "CREDIT", "CREDITS", "BAL"],
    "win":  ["WIN", "WINS", "WON", "TOTALWIN", "WINAMOUNT"],
    "bet":  ["BET", "BETS", "STAKE", "WAGER", "TOTALBET", "LINEBET"],
}

# Matches numbers like 1,250.00 / $45.50 / 980 / 2.00 / 1250
NUMERIC_RE = re.compile(r"^[\$₹€£]?\s?-?[\d,]+\.?\d{0,2}$")

FUZZY_CUTOFF = 0.72  # similarity threshold for label matching (handles OCR noise)

NUMERIC_WHITELIST = "0123456789.,$₹€£-"
LABEL_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# ---------------------------------------------------------------------------
# Configured regions of interest (ROIs)
# ---------------------------------------------------------------------------
# Each box is normalized [x0, y0, x1, y1] — fractions (0.0-1.0) of the
# image's width/height, NOT pixel coordinates. This keeps a defined region
# resolution-independent: the same box works whether the screenshot is
# 1080x1920 or 540x960, as long as the game's UI layout is proportionally
# the same. Multiply by (width, height) at crop time to get pixel coords.
#
# A region can list MULTIPLE candidate boxes, because different games put
# the meter bar in different places (e.g. just above the SERVICE/COLLECT
# row vs. just below the top jackpot ribbon). They're tried in order —
# see roi._locate_meter_roi_from_config — and the first one that actually
# yields a confidently-extracted field wins. If a screenshot doesn't match
# ANY listed box, the pipeline automatically falls back to the dynamic,
# color-based panel detection in slotocr.panel_detection / slotocr.roi —
# so this is a helpful shortcut, not a hard requirement.
#
# To support a new game layout: crop a sample screenshot to find the
# meter bar's pixel box, divide x's by the image width and y's by the
# image height, and append a new entry below. No other code changes
# needed.
ROI_REGIONS = {
    "meters": {
        "description": "CASH / WIN / BET credit meters",
        "boxes": [
            {
                "label": "bottom_bar",
                "notes": "Meter bar sits just above the SERVICE/COLLECT row, below the reels.",
                "box": [0.229264, 0.844468, 0.762349, 0.884554],
            },
            {
                "label": "hnpl_portrait",
                "notes": "HuffNPuffLink on the ICE cabinet, whose game window is 612x961 "
                         "(aspect 0.64, against the 0.58 the box above was tuned on). The "
                         "meter bar is the thin dark strip between the reels and the "
                         "SERVICE/COLLECT row. Without this the dynamic detector takes "
                         "over and returns most of the screen, which reads CASH but loses "
                         "BET. The bottom edge is deliberately 752 px and not 754: the "
                         "strip is only ~26 px tall, and two more rows of pixels pull the "
                         "bright COLLECT row into the crop, which moves the Otsu threshold "
                         "far enough to lose the BET value entirely. Swept 722-727 x "
                         "750-756 against both frames of a real run; every combination but "
                         "y1=754 reads cash and bet on both.",
                "box": [0.138889, 0.755463, 0.869281, 0.782518],
            },
        ],
    },
}
