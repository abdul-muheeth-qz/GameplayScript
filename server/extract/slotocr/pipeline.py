"""
The full per-image pipeline: locate the meter-bar ROI, run both extraction
methods on it, run the elimination pass, and fall back to a whole-image
method only if nothing else worked.
"""
import logging
import os

import cv2

from .config import FIELD_LABELS
from .ocr_utils import preprocess_for_ocr, run_ocr_data
from .roi import locate_meter_roi
from .matching import find_label_tokens, find_numeric_tokens, extract_field_value, clean_numeric_value
from .extraction import extract_all, run_elimination_pass

LOG = logging.getLogger("extract")


def save_roi_crop(image_path, roi_image, roi_dir):
    """Save the cropped meter-bar ROI so it can be visually inspected — e.g. to
    confirm the right region was located, or to debug why extraction failed on
    a given screenshot.

    The directory is a parameter rather than a constant. It used to be a bare
    relative "roi_crops", which resolves against the *current working
    directory*: run from a server and the crops land wherever the server was
    started, not beside the frames they came from.
    """
    if not roi_dir:
        return None
    try:
        os.makedirs(roi_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(image_path))[0]
        out_path = os.path.join(roi_dir, f"{base}_roi.png")
        # cv2.imwrite reports failure by RETURNING False, not by raising —
        # e.g. a non-ASCII output path on Windows. Without this check we'd
        # cheerfully report "Saved ROI crop to ..." for a file that isn't there.
        if not cv2.imwrite(out_path, roi_image):
            LOG.warning("could not save ROI crop: cv2.imwrite failed for %s", out_path)
            return None
        return out_path
    except Exception as e:
        LOG.warning("could not save ROI crop: %s", e)
        return None


def process_image(image_path, roi_dir=None):
    image = cv2.imread(image_path)
    if image is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")

    # ---- (0) Locate the meter-bar ROI and crop to it FIRST ----
    # detect_dark_panels/group_panels_into_rows scan the full screenshot,
    # but that's pure OpenCV (contour/color analysis) — no OCR. Once we
    # know which row of panels is the real CASH/WIN/BET bar, we crop down
    # to just that region and hand ONLY this small crop to Tesseract for
    # every OCR call from here on — never the full, visually busy
    # screenshot. This is faster and avoids background artwork confusing
    # the OCR engine.
    roi = locate_meter_roi(image)
    roi_image = roi.image
    img_h, img_w = roi_image.shape[:2]

    # Which of the three routes found the meter bar -- "config:<label>", "dynamic",
    # or "none" (the whole image). It rides out in the record rather than only being
    # logged, because it is the fastest way to tell a mis-tuned ROI box from a bad
    # OCR read when a value comes back wrong.
    LOG.info("%s: ROI located via %s", os.path.basename(image_path), roi.source)
    roi_path = save_roi_crop(image_path, roi_image, roi_dir)
    if roi_path:
        LOG.info("saved meter-bar ROI crop to %s", roi_path)

    result = {"image": os.path.basename(image_path),
              "roi_source": roi.source,
              "roi_crop": os.path.basename(roi_path) if roi_path else None}

    # ---- (a) Run both extraction methods over the ROI crop ----
    # PRIMARY (word-level OCR within each detected panel): finds candidate
    # meter panels by their visual properties (dark/flat background), then
    # runs sparse-text OCR (psm 11) inside each one and matches labels to
    # nearby currency-shaped values. The most general method: it handles both
    # "each meter is its own small box" layouts and "the whole meter bar is
    # one merged panel with an extra credit-count number sitting next to each
    # label" layouts.
    #
    # SECONDARY (per-cell number/label-band extraction): used only to fill in
    # fields the primary method missed. Works well when each meter is drawn as
    # its own small, cleanly-separated panel.
    #
    # When the ROI was chosen by validating a configured box, locating it
    # already ran both methods over these exact pixels — reuse that instead
    # of OCR'ing the same crop a second time.
    if roi.extracted is not None:
        panel_cell_results, panel_word_results, panel_currency_tokens = roi.extracted
    else:
        panel_cell_results, panel_word_results, panel_currency_tokens = extract_all(roi_image)

    panel_results = dict(panel_cell_results)
    panel_results.update(panel_word_results)  # word-OCR method takes priority

    # Elimination runs on the COMBINED result from both methods: even if
    # (say) balance and bet were only resolved by the older per-cell
    # method, a still-missing field can be recovered from a leftover
    # currency-shaped number sitting in a sibling panel within the same
    # trusted meter row.
    panel_results = run_elimination_pass(panel_results, panel_currency_tokens)

    for r in panel_results.values():
        r.pop("panel_idx", None)
        r.pop("row_id", None)
        r.pop("box", None)
        r.pop("token_pos", None)

    # ---- (b) LAST-RESORT FALLBACK: whole-image nearest label/value match --
    # Used only when NEITHER panel method found any confident meter panel
    # (e.g. unusual UI styles where meters aren't drawn as flat dark panels
    # at all). If either method found even one confidently-labeled cell we
    # trust the combined result fully — including fields left blank, since a
    # genuinely empty meter box (WIN showing nothing before a spin) is a
    # real, correct result, not a detection failure.
    fallback_results = {}
    if not panel_results:
        gray, binary, scale = preprocess_for_ocr(roi_image)
        data = run_ocr_data(gray, binary)
        numeric_tokens = find_numeric_tokens(data)
        for field in FIELD_LABELS:
            label_matches = find_label_tokens(data, FIELD_LABELS[field])
            best = extract_field_value(label_matches, numeric_tokens, img_w * scale, img_h * scale)
            if best:
                fallback_results[field] = {
                    "value": clean_numeric_value(best["value_token"]["text"]),
                    "rawtext": best["value_token"]["text"],
                    "confidence": best["value_token"]["conf"],
                    "label_matched": best["label"]["text"],
                }

    for field in FIELD_LABELS:
        if field in panel_results:
            result[field] = panel_results[field]
        elif field in fallback_results:
            result[field] = fallback_results[field]
        else:
            result[field] = {
                "value": None,
                "rawtext": "",
                "confidence": 0,
                "label_matched": None,
            }

    return result
