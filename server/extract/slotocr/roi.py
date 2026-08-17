"""
Locate the CASH/WIN/BET meter-bar region within a full screenshot, so the
rest of the pipeline (and every OCR call) can operate on a small crop
instead of the whole, visually busy image.

One method: a normalized [x0, y0, x1, y1] box per known game layout
(`roi_config.CONFIGURED_BOXES`). Exact, and works across every screenshot that
shares a layout, because the box is fractions of the frame rather than pixels.

Choosing between the boxes is `server.utils.crop_best_box` -- generic, and
knowing nothing about meters. What lives *here* is the half that is specific to
this stage: **how a crop is scored.** "Does this box apply?" is answered by
running the real extraction on it and counting the meter fields it resolved, so
each candidate costs a full extraction to validate; the winner's results ride
along on the returned MeterROI so the pipeline doesn't repeat that work.

**Nothing backs this method up, and nothing rescues a bad crop.** Two other
methods used to live here -- equal horizontal bands, and OpenCV dark-panel
detection -- with a `ROI_METHOD` constant selecting one of the three. An even
earlier version raced the box winner against dark-panel detection and switched
methods mid-image on a field count. That read well, and it made "which pixels
was this number read from?" a question only `roi_source` could answer after the
fact, while charging every ordinary frame for the losing methods. A crop that
misses the meter bar now shows up as null meters, which is the failure worth
having: it names the box that was handed to Tesseract instead of quietly
reporting a whole-image read nobody asked for.

The multi-box race here is not a fallback. Those boxes describe alternative
layouts of the same thing, and picking between them is what this method *is*.

This module sits ABOVE extraction (it imports it), which is why it's a separate
module rather than living in panel_detection -- extraction.py already depends
on panel_detection.py, so putting this here avoids a circular import while
still letting us reuse the proven label-matching logic from both extraction
methods (rather than re-implementing a weaker one-off check).
"""
import logging
from typing import NamedTuple, Optional

from ...utils import crop_best_box
from .roi_config import CONFIDENT_FIELDS, CONFIGURED_BOXES
from .extraction import extract_all

LOG = logging.getLogger("extract")

BOXES_NAME = "roi_config.CONFIGURED_BOXES"


class MeterROI(NamedTuple):
    """The located meter-bar crop, how it was found, and the extraction
    results for that exact crop.

    `extracted` is the (cell_results, word_results, currency_tokens) triple
    from extraction.extract_all: choosing between the boxes means running both
    extraction methods on these very pixels, so the answer is carried out here
    rather than thrown away and paid for twice downstream. It is Optional only
    so a caller constructing a MeterROI by hand need not supply it.
    """
    image: object
    source: str
    extracted: Optional[tuple] = None


def _fields_resolved(extracted):
    """How many distinct meter fields a (cell, word, tokens) triple resolved.

    A field the word method asserted BLANK does not count. It carries a real
    reading -- the label was found and its meter is empty -- but it resolved no
    value, and counting it would let a box win this race, and clear
    CONFIDENT_FIELDS, on the strength of meters it could not actually read.
    """
    cell_results, word_results, _ = extracted
    return len(set(cell_results)
               | {f for f, r in word_results.items() if not r.get("blank")})


def score_crop(crop):
    """How well one candidate crop reads, as `crop_best_box` wants it.

    Run the real extraction over these pixels and rank the crop by how many
    meter fields came back. The extraction itself is returned alongside,
    because it is the same work the pipeline is about to do -- see MeterROI.

    This is the whole downstream extraction, so scoring is NOT cheap. That is
    why `locate_meter_roi` passes `confident=CONFIDENT_FIELDS`.
    """
    extracted = extract_all(crop)
    return _fields_resolved(extracted), extracted


def locate_meter_roi(image):
    """Crop `image` to the best-reading box in `roi_config.CONFIGURED_BOXES`.

    Returns a MeterROI whose `source` is "config:<label>" -- the box that was
    actually cropped to. It rides out in the record because it is the fastest
    way to tell a mis-tuned ROI from a bad OCR read.

    The selection rules -- best box rather than first, ties to the earlier box,
    a frame where nothing scored still returning pixels -- are
    `server.utils.crop_best_box`'s, and are written up there.
    """
    best = crop_best_box(image, CONFIGURED_BOXES, score=score_crop,
                         confident=CONFIDENT_FIELDS, boxes_name=BOXES_NAME)
    if best.rank == 0:
        LOG.warning("no configured ROI box resolved a meter field; using config:%s "
                    "anyway. Add a box for this layout to %s", best.label, BOXES_NAME)
    return MeterROI(best.image, f"config:{best.label}", best.reading)
