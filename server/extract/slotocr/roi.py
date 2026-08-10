"""
Locate the CASH/WIN/BET meter-bar region within a full screenshot, so the
rest of the pipeline (and every OCR call) can operate on a small crop
instead of the whole, visually busy image.

Three methods, and `roi_config.ROI_METHOD` selects exactly one:

  1. HORIZONTAL BANDS (RoiMethod.BANDS) — the frame cut into
     `roi_config.BAND_COUNT` equal horizontal strips, of which
     `roi_config.BANDS` are kept. Hand-specified, therefore believed: no OCR
     runs to decide anything, which makes this the cheapest of the three —
     the pipeline's own extraction of the crop is the only one that happens.

  2. CONFIGURED BOX (RoiMethod.CONFIGURED) — a normalized [x0, y0, x1, y1]
     box per known game layout (`roi_config.CONFIGURED_BOXES`). Exact, and
     works across screenshots that share a layout. "Does this box apply?" is
     answered by running the real extraction on it, so each candidate costs a
     full extraction to validate; the winner's results ride along on the
     returned MeterROI so the pipeline doesn't repeat that work.

  3. DYNAMIC DETECTION (RoiMethod.DYNAMIC) — no fixed coordinates at all,
     just "what does this screenshot's UI actually look like": OpenCV finds
     the flat dark panel rows, and both extraction methods vote on which row
     is really the meter bar.

**There is no fallback between the three.** An earlier version tried the
configured boxes and then raced the winner against dynamic detection,
switching methods mid-image on a field count. It read well, but it made "which
pixels was this number read from?" a question only `roi_source` could answer
after the fact, and it charged every ordinary frame for the losing methods.
The method is now chosen up front, and a crop that misses the meter bar shows
up as null meters rather than being quietly rescued.

The multi-box race *within* method 2 is not a fallback and stays: those boxes
describe alternative layouts of the same thing, and picking between them is
what method 2 is.

This module sits ABOVE panel_detection and extraction (it imports both),
which is why it's a separate module rather than living in panel_detection
— extraction.py already depends on panel_detection.py, so putting this
here avoids a circular import while still letting us reuse the proven
label-matching logic from both extraction methods (rather than
re-implementing a weaker one-off check).
"""
import logging
from typing import NamedTuple, Optional

from .roi_config import (BAND_COUNT, BANDS, CONFIDENT_FIELDS, CONFIGURED_BOXES,
                         DYNAMIC_MIN_PAD_Y, DYNAMIC_PAD_FRAC_X, DYNAMIC_PAD_FRAC_Y,
                         ROI_METHOD, RoiMethod)
from .panel_detection import detect_dark_panels, group_panels_into_rows
from .extraction import extract_all

LOG = logging.getLogger("extract")


class MeterROI(NamedTuple):
    """The located meter-bar crop, how it was found, and (when we already
    know them) the extraction results for that exact crop.

    `extracted` is the (cell_results, word_results, currency_tokens) triple
    from extraction.extract_all when the method already ran both extraction
    methods on these very pixels to *choose* this crop — i.e. the configured
    box race. The pipeline should reuse that answer rather than pay for it
    twice. It is None for the methods that need no OCR to decide (bands) or
    that ran their OCR against different pixels (dynamic detection scores
    rows on the full screenshot, so those results say nothing about the crop
    it returns).
    """
    image: object
    source: str
    extracted: Optional[tuple] = None


def crop_normalized_box(image, box):
    """Crop `image` to a normalized [x0, y0, x1, y1] box (fractions of
    width/height, 0.0-1.0). Returns None if the box is empty or inverted."""
    h_img, w_img = image.shape[:2]
    x0f, y0f, x1f, y1f = box
    x0 = max(0, min(w_img, int(round(x0f * w_img))))
    y0 = max(0, min(h_img, int(round(y0f * h_img))))
    x1 = max(0, min(w_img, int(round(x1f * w_img))))
    y1 = max(0, min(h_img, int(round(y1f * h_img))))
    if x1 <= x0 or y1 <= y0:
        return None
    return image[y0:y1, x0:x1]


def _fields_resolved(extracted):
    """How many distinct meter fields a (cell, word, tokens) triple resolved."""
    cell_results, word_results, _ = extracted
    return len(set(cell_results) | set(word_results))


# ---------------------------------------------------------------------------
# Method 1 -- horizontal bands
# ---------------------------------------------------------------------------

def normalize_bands(bands, count):
    """`roi_config.BANDS` as an inclusive (first, last) pair, whichever of its
    two shapes it was written in, checked against `count`.

    A band outside 1..count is a typo in a hand-edited constant, and the
    failure it would otherwise cause is silent: crop_normalized_box clamps to
    the image, so BANDS = 25 of 24 would crop the bottom row of pixels and
    every meter would read blank with nothing to say why. Name the constant to
    fix instead.
    """
    first, last = (bands, bands) if isinstance(bands, int) else tuple(bands)
    if count < 1:
        raise ValueError(f"roi_config.BAND_COUNT is {count!r}; it must be at least 1")
    if not 1 <= first <= last <= count:
        raise ValueError(
            f"roi_config.BANDS is {bands!r}, which is not a band or a range of "
            f"consecutive bands within 1..{count} (roi_config.BAND_COUNT). Bands are "
            f"numbered from 1 at the top of the frame, and a range is an inclusive "
            f"(first, last) pair")
    return first, last


def crop_horizontal_bands(image, count, first, last):
    """Crop `image` to bands `first`..`last` (inclusive, 1-based) of `count`
    equal, full-width horizontal strips.

    Expressed as a normalized box and cropped by the same code as a configured
    box, so a band and a box cannot round to different pixels for the same
    edge. The edges come from the fractions rather than from a per-band pixel
    height, which is what keeps band N's top edge exactly on band N-1's bottom
    edge when `count` does not divide the height evenly.
    """
    return crop_normalized_box(image, [0.0, (first - 1) / count, 1.0, last / count])


def _locate_meter_roi_bands(image, count=BAND_COUNT, bands=BANDS):
    """Crop to the configured horizontal band(s). No OCR, no scoring: the
    band was named by hand, so it is what gets used."""
    first, last = normalize_bands(bands, count)
    label = f"{first}" if first == last else f"{first}-{last}"
    crop = crop_horizontal_bands(image, count, first, last)
    if crop is None or crop.size == 0:
        h_img, w_img = image.shape[:2]
        raise ValueError(
            f"band {label} of {count} is empty on a {w_img}x{h_img} frame -- {count} "
            f"bands over {h_img} px rounds to less than a pixel each; lower "
            f"roi_config.BAND_COUNT")
    return MeterROI(crop, f"bands:{label}/{count}")


# ---------------------------------------------------------------------------
# Method 2 -- configured boxes
# ---------------------------------------------------------------------------

def _locate_meter_roi_from_config(image):
    """Try the candidate boxes in `roi_config.CONFIGURED_BOXES` and keep the
    one that reads best.

    Validating a box means running both extraction methods on it and seeing
    what they resolved — so validation is NOT cheap, it is the whole
    downstream extraction. Those results are therefore returned along with
    the winning crop instead of being discarded, so the pipeline can reuse
    them rather than OCR the same pixels a second time.

    Boxes are tried in order and the one resolving the MOST fields wins, rather
    than the first one that resolved any. Boxes describe different games'
    layouts, and a box aimed at another layout can easily land on some
    unrelated part of this screenshot and still scrape one plausible number
    out of it — under a first-past-the-post rule, adding a box for a new
    cabinet then silently degraded every screenshot listed ahead of it.
    Ties go to the earlier box, so the list order still reads as a
    preference order, and the search stops as soon as a box clears
    CONFIDENT_FIELDS rather than paying for the rest of the list.

    With no dynamic detection behind it any more, a run where NO box resolved
    a single field still has to return pixels. It returns the first usable
    box's crop — the head of that preference order — and warns, so the record
    names the box that was actually handed to Tesseract instead of reporting
    a whole-image read the caller never asked for.
    """
    best = None
    # -1, not 0, so the first usable candidate always becomes `best`: a box that
    # resolved nothing is still the crop this method chose, and returning None
    # here would leave the pipeline with no image at all.
    best_score = -1
    for candidate in CONFIGURED_BOXES:
        crop = crop_normalized_box(image, candidate["box"])
        if crop is None or crop.size == 0:
            continue
        extracted = extract_all(crop)
        score = _fields_resolved(extracted)
        if score > best_score:
            label = candidate.get("label", "unnamed")
            best, best_score = MeterROI(crop, f"config:{label}", extracted), score
        if best_score >= CONFIDENT_FIELDS:
            break
    if best is None:
        raise ValueError(
            "no box in roi_config.CONFIGURED_BOXES could be cropped from this frame -- "
            "the list is empty, or every box in it is inverted or off the image")
    if best_score == 0:
        LOG.warning("no configured ROI box resolved a meter field; using %s anyway "
                    "(roi_config.ROI_METHOD is CONFIGURED, which has no fallback). Add a "
                    "box for this layout, or try RoiMethod.DYNAMIC on it", best.source)
    return best


# ---------------------------------------------------------------------------
# Method 3 -- dynamic detection
# ---------------------------------------------------------------------------

def _locate_meter_roi_dynamic(image, pad_frac_x=DYNAMIC_PAD_FRAC_X,
                              pad_frac_y=DYNAMIC_PAD_FRAC_Y,
                              min_pad_y=DYNAMIC_MIN_PAD_Y):
    """Find which detected panel-row is most likely the CASH/WIN/BET meter
    bar, and return a CROPPED sub-image covering just that region.

    `detect_dark_panels` runs on the FULL screenshot here — but that's
    pure OpenCV contour/color analysis, no OCR involved. To decide WHICH
    row is the real meter bar (as opposed to a row of jackpot badges, or a
    row of buttons), we run BOTH extraction methods on the full image and
    see which row(s) they actually managed to resolve fields in — the row
    with the most resolved fields wins. This reuses the same proven label-
    matching logic the rest of the pipeline relies on, rather than a
    separate, weaker one-off check.

    Returns a MeterROI. Its `extracted` is always None: the extraction here
    ran against the FULL image, whereas the pipeline needs results for the
    returned CROP, so those results are not reusable downstream.

    When no row confidently looks like a meter bar it returns the original,
    uncropped image, with source "dynamic:whole-image" so that shows up in the
    record. That is not a fallback to another method — it is this method
    reporting that it found nothing to crop to, while still handing the
    pipeline's own whole-image OCR pass something to work with.
    """
    h_img, w_img = image.shape[:2]
    rows = group_panels_into_rows(detect_dark_panels(image))
    whole_image = MeterROI(image, "dynamic:whole-image")
    if not rows:
        return whole_image

    row_scores = {i: 0 for i in range(len(rows))}

    def tally(results):
        for r in results.values():
            row_id = r.get("row_id")
            # Only count rows we can actually index back into `rows`.
            # extract_fields_via_panel_word_ocr falls back to row_id -1 for a
            # panel it couldn't place in a row; letting that (or any other
            # unexpected id) into row_scores would make the max() below select
            # a key that isn't a valid row index — silently cropping to
            # rows[-1], the bottom-most row, or raising IndexError.
            if row_id in row_scores:
                row_scores[row_id] += 1

    cell_results, word_results, _ = extract_all(image)
    tally(cell_results)
    tally(word_results)

    best_row_id = max(row_scores, key=row_scores.get)
    if row_scores[best_row_id] == 0:
        # No row produced a single confident field match anywhere — hand
        # back the whole image so the pipeline's own whole-image fallback
        # still gets a chance, rather than silently cropping to nothing useful.
        return whole_image

    best_row = rows[best_row_id]
    xs0 = min(b[0] for b in best_row)
    ys0 = min(b[1] for b in best_row)
    xs1 = max(b[0] + b[2] for b in best_row)
    ys1 = max(b[1] + b[3] for b in best_row)

    pad_x = max(10, int((xs1 - xs0) * pad_frac_x))
    pad_y = max(min_pad_y, int((ys1 - ys0) * pad_frac_y))

    x0 = max(0, xs0 - pad_x)
    y0 = max(0, ys0 - pad_y)
    x1 = min(w_img, xs1 + pad_x)
    y1 = min(h_img, ys1 + pad_y)

    roi = image[y0:y1, x0:x1]
    if roi.size == 0:
        return whole_image
    return MeterROI(roi, "dynamic")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
# One entry per RoiMethod member. Adding a fourth cropping method is a member
# on the enum, a _locate_meter_roi_* function and a line here -- nothing in the
# pipeline changes, because roi_source is a string it only passes through.
_METHODS = {
    RoiMethod.BANDS: _locate_meter_roi_bands,
    RoiMethod.CONFIGURED: _locate_meter_roi_from_config,
    RoiMethod.DYNAMIC: _locate_meter_roi_dynamic,
}


def locate_meter_roi(image, method=None):
    """Crop `image` to the meter bar using exactly one method: `method` when
    given (a RoiMethod or its string value), otherwise `roi_config.ROI_METHOD`.

    No method backs up another. The selected one either finds the meter bar or
    the record says it didn't.

    Returns a MeterROI whose `source` names what ran and what it chose:
    "bands:<first>[-<last>]/<count>", "config:<label>", "dynamic", or
    "dynamic:whole-image".
    """
    try:
        selected = RoiMethod(ROI_METHOD if method is None else method)
    except ValueError:
        # A bad string from the CLI or (later) config.json. argparse `choices`
        # catches the CLI case first; this is the one that reaches a caller
        # passing the value straight through.
        raise ValueError(
            f"{method!r} is not an ROI cropping method; roi_config.RoiMethod has "
            f"{', '.join(repr(m.value) for m in RoiMethod)}") from None
    return _METHODS[selected](image)
