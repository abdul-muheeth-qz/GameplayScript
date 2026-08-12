"""
Which ROI cropping method runs, and the hand-tuned numbers each one needs.

`ROI_METHOD` will eventually come from config.json and the UI. It is a plain
constant for now so that there is exactly one place to change it, and so the
three methods can be compared from the command line:

    python -m server.extract.cli server/extract/Images --roi-method bands
"""
from enum import Enum


class RoiMethod(str, Enum):
    """The three ways to crop the meter strip out of a frame.

    A `str` Enum so the value doubles as the CLI argument and the string that
    would come out of config.json, and so `RoiMethod("bands")` accepts either
    form without a lookup table.
    """
    BANDS = "bands"
    CONFIGURED = "configured"
    DYNAMIC = "dynamic"


ROI_METHOD = RoiMethod.CONFIGURED


# ---------------------------------------------------------------------------
# Method 1 -- horizontal bands
# ---------------------------------------------------------------------------
# Slice the frame into BAND_COUNT equal horizontal strips numbered 1..N from
# the TOP, and crop the ones BANDS names. Every band is full width, because a
# horizontal band is a slice of the height: only y moves.

BAND_COUNT = 24
BANDS = 19


# ---------------------------------------------------------------------------
# Method 2 -- configured boxes
# ---------------------------------------------------------------------------
# Each box is normalized [x0, y0, x1, y1] -- fractions (0.0-1.0) of the
# image's width and height.
#
# The list holds one box per game layout, because different games put the
# meter bar in different places (just above the SERVICE/COLLECT row vs. just
# below the top jackpot ribbon). They are all tried against the screenshot and
# the one that READS BEST wins, not the first that resolved anything -- see
# roi._locate_meter_roi_from_config, where that rule and the `notes` below
# were each bought with a wrong reading.
#
# To support a new game layout: crop a sample screenshot to find the meter
# bar's pixel box, divide the x's by the image width and the y's by the image
# height, and append an entry here. No code changes needed.
CONFIGURED_BOXES = [
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
                 "SERVICE/COLLECT row. The bottom edge is deliberately 752 px and "
                 "not 754: the strip is only ~26 px tall, and two more rows of "
                 "pixels pull the bright COLLECT row into the crop, which moves the "
                 "Otsu threshold far enough to lose the BET value entirely. Swept "
                 "722-727 x 750-756 against both frames of a real run; every "
                 "combination but y1=754 reads cash and bet on both.",
        "box": [0.138889, 0.755463, 0.869281, 0.782518],
    },
]

# How many fields a configured box has to resolve before it is believed outright
# and the rest of the list is skipped.
#
# Not "all of them": WIN is genuinely blank on most before-frames, so a box that has
# found the meter bar perfectly still comes back with two. Requiring three meant every
# ordinary frame went on to try every remaining box as well, and took 27 s a pair
# instead of 3.
#
# Two is the line because one is exactly what a *wrong* box looks like: a box tuned for
# another game's layout lands somewhere unrelated and scrapes a single plausible number
# out of it. Two fields in one crop is a meter bar.
CONFIDENT_FIELDS = 2


DYNAMIC_PAD_FRAC_X = 0.03
DYNAMIC_PAD_FRAC_Y = 0.6
DYNAMIC_MIN_PAD_Y = 40
