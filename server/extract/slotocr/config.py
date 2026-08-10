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
# Where the ROI numbers went
# ---------------------------------------------------------------------------
# Everything about *locating* the meter strip -- which of the three cropping
# methods runs, the horizontal-band counts, and the normalized boxes that used
# to be ROI_REGIONS here -- lives in slotocr/roi_config.py. This file is the
# constants for *reading* the crop once it exists.
