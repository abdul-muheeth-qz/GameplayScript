"""The per-image pipeline: crop to the meter ROI, run both extraction methods over it, run the
elimination pass, and fall back to a whole-image method only if nothing else worked."""
import logging
import os

import cv2

from .config import FIELD_LABELS
from .ocr_utils import preprocess_for_ocr, run_ocr_data
from .roi import locate_meter_roi
from .matching import (find_label_tokens, find_numeric_tokens, find_text_tokens,
                       extract_field_value, clean_numeric_value)
from .extraction import extract_all, run_elimination_pass

LOG = logging.getLogger("extract")


def save_roi_crop(image_path, roi_image, roi_dir):
    """Save the meter ROI crop for inspection -- whether the right region was located at all.

    The directory is a parameter, not a constant: a bare relative "roi_crops" resolves against the
    CWD, so run from a server the crops landed wherever the server was started.
    """
    if not roi_dir:
        return None
    try:
        os.makedirs(roi_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(image_path))[0]
        out_path = os.path.join(roi_dir, f"{base}_roi.png")
        # cv2.imwrite reports failure by RETURNING False, not by raising (a non-ASCII path on
        # Windows, say), so without this check a missing file is reported as saved.
        if not cv2.imwrite(out_path, roi_image):
            LOG.warning("could not save ROI crop: cv2.imwrite failed for %s", out_path)
            return None
        return out_path
    except Exception as e:
        LOG.warning("could not save ROI crop: %s", e)
        return None


def process_image(image_path, roi_dir=None, cfg=None):
    """Read the meters off one screenshot.

    `cfg` goes straight to `roi.locate_meter_roi`, which crops to the active game's `meter_roi` and
    raises by name when there is no active game or no box on it.
    """
    image = cv2.imread(image_path)
    if image is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")

    # Crop first, and hand ONLY this small crop to tesseract from here on -- never the full, busy
    # screenshot. Faster, and background artwork cannot confuse the engine.
    roi = locate_meter_roi(image, cfg)
    roi_image = roi.image
    img_h, img_w = roi_image.shape[:2]

    LOG.info("%s: ROI located via %s", os.path.basename(image_path), roi.source)
    roi_path = save_roi_crop(image_path, roi_image, roi_dir)
    if roi_path:
        LOG.info("saved meter-bar ROI crop to %s", roi_path)

    result = {"image": os.path.basename(image_path),
              "roi_source": roi.source,
              "roi_crop": os.path.basename(roi_path) if roi_path else None}

    # Both methods over the crop. The word-OCR one is primary and the more general -- it handles
    # separate small boxes and one merged bar alike; the per-cell one only fills in what it missed.
    panel_cell_results, panel_word_results, panel_currency_tokens = extract_all(roi_image)

    panel_results = dict(panel_cell_results)
    panel_results.update(panel_word_results)  # word-OCR method takes priority

    # On the COMBINED result, so a field can be recovered from a sibling panel in the same trusted
    # row even when the other fields were resolved by the per-cell method.
    panel_results = run_elimination_pass(panel_results, panel_currency_tokens)

    for r in panel_results.values():
        r.pop("panel_idx", None)
        r.pop("row_id", None)
        r.pop("box", None)
        r.pop("token_pos", None)
        # "blank" is internal: an empty meter leaves the same all-null record the not-found branch
        # below writes, so the output contract is unchanged.
        r.pop("blank", None)

    # Last resort, only when NEITHER panel method found a confident panel. One confidently-labelled
    # cell is enough to trust the combined result fully, blank fields included -- an empty WIN
    # before a spin is a correct reading, not a detection failure.
    fallback_results = {}
    if not panel_results:
        gray, binary, scale = preprocess_for_ocr(roi_image)
        data = run_ocr_data(gray, binary)
        numeric_tokens = find_numeric_tokens(data)
        # Every lettery token, not just recognised titles: a title OCR could not read still marks
        # where the next cell starts.
        all_labels = find_text_tokens(data)
        for field in FIELD_LABELS:
            label_matches = find_label_tokens(data, FIELD_LABELS[field])
            best = extract_field_value(label_matches, numeric_tokens,
                                       img_w * scale, img_h * scale, all_labels)
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
