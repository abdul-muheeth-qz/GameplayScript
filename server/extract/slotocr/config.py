"""Constants for *reading* the meter crop. Locating it is game_config.json's `meter_roi`."""
import re

# The keys are the interface: they come out of this stage and `validate` reads them, which is why
# the cash meter's key is "cash" and not "balance". A rename here is the single point of change.
# The lists are OCR *synonyms* -- what tesseract might have read -- so BALANCE stays in one.
FIELD_LABELS = {
    "cash": ["BALANCE", "CASH", "CREDIT", "CREDITS", "BAL"],
    "win":  ["WIN", "WINS", "WON", "TOTALWIN", "WINAMOUNT"],
    "bet":  ["BET", "BETS", "STAKE", "WAGER", "TOTALBET", "LINEBET"],
}

# Matches numbers like 1,250.00 / $45.50 / 980 / 2.00 / 1250
NUMERIC_RE = re.compile(r"^[\$₹€£]?\s?-?[\d,]+\.?\d{0,2}$")

# A *complete* amount, which is how find_numeric_tokens tells a whole one from a torn one:
# NUMERIC_RE's `\.?\d{0,2}` tail makes both the point and the cents optional, so "$2,190" (half a
# shredded "$2,190.90") and "6." (debris) pass it as readily as "$2,182.95".
#
# The assumption, since it is the one thing that would make this wrong: every meter here draws
# exactly two decimals, "." as the point and "," only grouping thousands, verified across all 42
# captured records and both fixture layouts. Hence the trailing separator must be a period --
# `[.,]\d{2}` would accept "$2,18", the truncated half this exists to catch, and no regex tells
# that from a European "20,00". Relax here if such a layout appears, at the cost of catching a
# comma-truncated fragment. `[\d,.]*` in the head keeps "1.175.76", which clean_numeric_value repairs.
COMPLETE_AMOUNT_RE = re.compile(r"^[\$₹€£]?\s?-?[\d,.]*\d\.\d{2}$")

FUZZY_CUTOFF = 0.72  # label matching similarity, loose enough for OCR noise

NUMERIC_WHITELIST = "0123456789.,$₹€£-"
LABEL_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
