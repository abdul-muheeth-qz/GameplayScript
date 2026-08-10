"""
Locate the CASH/WIN/BET meter-bar region within a full screenshot, so the
rest of the pipeline (and every OCR call) can operate on a small crop
instead of the whole, visually busy image.

Two strategies:

  1. CONFIGURED REGION (slotocr.config.ROI_REGIONS["meters"]) — a
     normalized [x0, y0, x1, y1] box (fractions of width/height). Exact,
     and works well across screenshots that share the same UI layout/aspect
     ratio. Tried first because, when it applies, it's more reliable than
     guessing — note that "does this box apply?" is answered by running the
     real extraction on it, so a configured box costs a full extraction to
     validate. When one matches, its results ride along on the returned
     MeterROI so the pipeline doesn't repeat that work.

  2. DYNAMIC DETECTION (color/contour-based panel detection) — no fixed
     coordinates, just "what does this screenshot's UI actually look like".

The order between them is not simply "config, then dynamic if it found
nothing". A box that found *one* value has not necessarily found the meter
bar — boxes describe several different games' layouts, and one aimed at
another layout can land somewhere unrelated on this screenshot and still
scrape a plausible number out of it. So a box is believed outright once it
clears CONFIDENT_FIELDS, and otherwise has to beat dynamic detection on the
same measure: how many meter fields the crop actually yields.

This module sits ABOVE panel_detection and extraction (it imports both),
which is why it's a separate module rather than living in panel_detection
— extraction.py already depends on panel_detection.py, so putting this
here avoids a circular import while still letting us reuse the proven
label-matching logic from both extraction methods (rather than
re-implementing a weaker one-off label check).
"""
from typing import NamedTuple, Optional

from .config import ROI_REGIONS
from .panel_detection import detect_dark_panels, group_panels_into_rows
from .extraction import extract_all


class MeterROI(NamedTuple):
    """The located meter-bar crop, how it was found, and (when we already
    know them) the extraction results for that exact crop.

    `extracted` is the (cell_results, word_results, currency_tokens) triple
    from extraction.extract_all when the ROI was chosen by *validating* a
    configured box — i.e. we already ran both methods on these very pixels,
    so the pipeline should reuse the answer rather than pay for it twice.
    It is None when the ROI came from anywhere else and no such results
    exist for this crop.
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


# How many fields a configured box has to resolve before it is believed outright.
#
# Not "all of them": WIN is genuinely blank on most before-frames, so a box that has
# found the meter bar perfectly still comes back with two. Requiring three meant every
# ordinary frame went on to run the dynamic race as well -- two more extractions, one
# of them over the whole screenshot -- and took 27 s a pair instead of 3.
#
# Two is the line because one is exactly what a *wrong* box looks like: a box tuned for
# another game's layout lands somewhere unrelated and scrapes a single plausible number
# out of it. Two fields in one crop is a meter bar.
CONFIDENT_FIELDS = 2


def _fields_resolved(extracted):
    """How many distinct meter fields a (cell, word, tokens) triple resolved."""
    cell_results, word_results, _ = extracted
    return len(set(cell_results) | set(word_results))


def _locate_meter_roi_from_config(image, region_key="meters"):
    """Try every configured candidate box for `region_key` (see the "boxes"
    list in slotocr.config.ROI_REGIONS) and keep the one that reads best.

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

    Returns the winning MeterROI, or None if no box resolved anything."""
    region = ROI_REGIONS.get(region_key)
    if not region:
        return None
    best = None
    best_score = 0
    for candidate in region.get("boxes", []):
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
    return best


def _locate_meter_roi_dynamic(image, pad_frac_x=0.03, pad_frac_y=0.6, min_pad_y=40):
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

    Falls back to returning the original, uncropped image (source "none")
    if no row confidently looks like a meter bar, so nothing is ever lost.
    """
    h_img, w_img = image.shape[:2]
    rows = group_panels_into_rows(detect_dark_panels(image))
    whole_image = MeterROI(image, "none")
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
        # back the whole image so downstream fallback methods still get a
        # chance, rather than silently cropping to nothing useful.
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


def locate_meter_roi(image, region_key="meters"):
    """Top-level ROI locator: the configured region's candidate boxes, dynamic
    color-based detection, and the whole image as an absolute last resort.

    A configured box that resolved CONFIDENT_FIELDS or more is taken immediately
    -- it is exact, it is cheap, and its extraction results are reusable. One
    that found a single lonely value is raced against dynamic detection and only
    kept if it reads at least as well, because "this box found a number" is not
    the same as "this box found the meter bar": a box tuned for another game's
    layout can land somewhere unrelated and still scrape one plausible value out
    of it. Ties go to the configured box, which is the more repeatable of the two.

    Returns a MeterROI whose `source` is "config:<label>" (naming which
    configured box matched), "dynamic", or "none"."""
    from_config = _locate_meter_roi_from_config(image, region_key)
    if from_config is not None and _fields_resolved(from_config.extracted) >= CONFIDENT_FIELDS:
        return from_config

    dynamic = _locate_meter_roi_dynamic(image)
    if from_config is None:
        return dynamic

    # _locate_meter_roi_dynamic extracted against the FULL image to choose a
    # row, so those results say nothing about the crop it returned. Re-extract
    # on the crop itself -- that is the comparison that matters, and it is the
    # work the pipeline would have done next anyway, so it rides along.
    dynamic_extracted = extract_all(dynamic.image)
    if _fields_resolved(dynamic_extracted) > _fields_resolved(from_config.extracted):
        return MeterROI(dynamic.image, dynamic.source, dynamic_extracted)
    return from_config
