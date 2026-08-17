"""
Shared configuration/constants for the slot-meter OCR pipeline.
"""
import re

# Label synonyms for each logical meter field we want to extract.
#
# The keys are the field names that come out the other end, and they are what
# the validate stage reads (validate/records.py's PREVIOUS_FIELDS and
# CURRENT_FIELDS). The cash meter's key is
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

# A *complete* amount: the decimal point followed by exactly two digits, with at
# least one digit in front of it. NUMERIC_RE cannot tell a whole amount from a
# torn one -- its `\.?\d{0,2}` tail makes both the point and the cents optional,
# so "$2,190" (the left half of a shredded "$2,190.90") and "6." (debris) pass it
# just as "$2,182.95" does. find_numeric_tokens uses this to tell the two apart.
#
# The named assumption, because it is the one thing that would make this wrong:
# EVERY meter on this cabinet and on the bottom_bar layout draws exactly two
# decimal places, with "." as the decimal point and "," only ever grouping
# thousands -- verified across all 42 captured records and both fixture layouts.
# Hence the trailing separator must be a period: `[.,]\d{2}` would also accept
# "$2,18", which is precisely the truncated half this exists to catch, and no
# regex can tell that from a European "20,00" drawn by some future game. If such
# a layout ever appears this is the single place to relax, and the relaxation
# costs the ability to detect a comma-truncated fragment.
#
# `[\d,.]*` rather than `[\d,]*` in the head keeps "1.175.76" -- the
# thousands-comma-misread-as-a-period case that clean_numeric_value repairs.
COMPLETE_AMOUNT_RE = re.compile(r"^[\$₹€£]?\s?-?[\d,.]*\d\.\d{2}$")

FUZZY_CUTOFF = 0.72  # similarity threshold for label matching (handles OCR noise)

NUMERIC_WHITELIST = "0123456789.,$₹€£-"
LABEL_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# ---------------------------------------------------------------------------
# Where the ROI numbers went
# ---------------------------------------------------------------------------
# Everything about *locating* the meter strip -- which of the three cropping
# methods runs, the horizontal-band counts, and the normalized boxes that used
# to be ROI_REGIONS here -- lives in slotocr/roi_config.py. This file is the
# constants for *reading* the crop once it exists.
