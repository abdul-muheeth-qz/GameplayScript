"""
Low-level OCR helpers: preprocessing images for tesseract and running OCR
on small already-localized crops.

Nothing in this module assumes fixed coordinates or a specific game layout
— these are generic image-preprocessing/OCR-calling utilities used by the
higher-level panel-detection and extraction modules.
"""
import re

import cv2
import numpy as np
import pytesseract
from pytesseract import Output


# ---------------------------------------------------------------------------
# Shared helpers for reading tesseract's image_to_data output
# ---------------------------------------------------------------------------
def token_conf(value):
    """One entry of an image_to_data "conf" column, as a float.

    Tesseract reports -1 for rows carrying no text, and pytesseract's
    `Output.DICT` already coerces this column with `int(float(...))` — so
    these are numbers, not the strings this code used to compare them
    against. Anything unparseable is reported as -1, i.e. "no confidence".
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1.0


def token_confidences(data, require_text=False):
    """Confidence values from an image_to_data dict, with tesseract's -1
    placeholder rows dropped."""
    return [
        conf
        for conf, text in zip(map(token_conf, data["conf"]), data["text"])
        if conf >= 0 and (not require_text or text.strip())
    ]


def best_ocr_variant(variants, config):
    """Run image_to_data over several preprocessing variants of the SAME
    image and keep whichever scored best — mean token confidence, plus a
    small bonus rewarding variants that find more tokens.

    Shared by run_ocr_data and panel_word_ocr, which are otherwise the same
    "try both polarities, keep the better one" routine over different
    preprocessing.
    """
    best_data, best_score = None, -1.0
    for variant in variants:
        data = pytesseract.image_to_data(
            variant, config=config, output_type=Output.DICT
        )
        confs = token_confidences(data)
        score = sum(confs) / len(confs) if confs else 0.0
        # slightly reward variants that find more tokens
        score += 0.01 * len([t for t in data["text"] if t.strip()])
        if score > best_score:
            best_score, best_data = score, data
    return best_data


# ---------------------------------------------------------------------------
# Whole-image preprocessing (used only by the last-resort fallback method)
# ---------------------------------------------------------------------------
def preprocess_for_ocr(image):
    """Return a cleaned-up, upscaled, high-contrast grayscale image
    that is easier for tesseract to read on typical slot-game UI text
    (bright text on dark/glowing backgrounds)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Upscale — slot meter text is often small relative to the screenshot.
    scale = 2
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # Improve local contrast (helps with glowing/gradient UI text).
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    # Denoise slightly.
    gray = cv2.bilateralFilter(gray, 5, 50, 50)

    # Otsu threshold -> binary image. Try both polarities and let tesseract's
    # own confidence tell us which is better (done later in run_ocr_data).
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    return gray, binary, scale


def run_ocr_data(gray, binary):
    """Run image_to_data on a couple of preprocessing variants and keep
    whichever produced more/higher-confidence tokens (dynamic, no assumption
    about a fixed 'best' preprocessing per screenshot)."""
    return best_ocr_variant([gray, binary], "--psm 11")


# ---------------------------------------------------------------------------
# Small-crop OCR helpers, used by both extraction methods
# ---------------------------------------------------------------------------
def remove_long_lines(binary_text_white, frac=0.55):
    """Strip long thin border/frame lines while preserving glyph strokes,
    which are much shorter than the panel width."""
    w = binary_text_white.shape[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, int(w * frac)), 1))
    lines = cv2.morphologyEx(binary_text_white, cv2.MORPH_OPEN, kernel)
    return cv2.subtract(binary_text_white, lines)


SMALL_CROP_UPSCALE = 5


def ocr_small_crop(crop, psms, whitelist=None):
    """OCR a small, already-localized crop (a single panel's number or
    label region), trying each PSM mode in `psms` (single line/word/sparse)
    and keeping the result with the most extracted characters, since
    stylized UI fonts can trip up one segmentation mode but not another.
    Returns (text, confidence)."""
    if crop is None or crop.size == 0:
        return "", 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    gray_up = cv2.resize(gray, None, fx=SMALL_CROP_UPSCALE, fy=SMALL_CROP_UPSCALE,
                         interpolation=cv2.INTER_CUBIC)
    _, text_white = cv2.threshold(gray_up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    text_white = remove_long_lines(text_white)
    text_white = cv2.morphologyEx(text_white, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    inv = cv2.bitwise_not(text_white)

    whitelist_cfg = f" -c tessedit_char_whitelist={whitelist}" if whitelist else ""

    best_text, best_conf, best_len = "", 0.0, -1
    for p in psms:
        config = f"--psm {p}{whitelist_cfg}"
        data = pytesseract.image_to_data(inv, config=config, output_type=Output.DICT)
        words = [t for t in data["text"] if t.strip()]
        confs = token_confidences(data, require_text=True)
        text = " ".join(words).strip()
        conf = sum(confs) / len(confs) if confs else 0.0
        cleaned_len = len(re.sub(r"[^A-Z0-9]", "", text.upper()))
        if cleaned_len > best_len:
            best_text, best_conf, best_len = text, conf, cleaned_len

    return best_text, best_conf


def _panel_ocr_scale(height):
    """3x assumes the panel's glyphs are already a reasonable size relative to
    the crop. That fails on a thin, tightly-cropped merged meter bar (~28px
    tall holding all three labels and values): at 3x-5x, the small BET label
    dissolves into unrecognizable garbage ("ee", "le") rather than text at
    all, permanently losing the field. At 6x it comes through consistently as
    "8ET" (with the panel's vertical divider line glued on) -- close enough
    for label_similarity's digit/letter lookalike handling to recover "BET".
    Only genuinely short panels get the boost; anything already a reasonable
    size keeps the default every other layout was tuned against."""
    return 6 if height <= 32 else 3


def panel_word_ocr(crop):
    """OCR a panel crop (or padded sub-region) with psm 11 (sparse text)
    and return tesseract's image_to_data dict, trying both black-on-white
    and white-on-black polarity and keeping whichever scores better."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    scale = _panel_ocr_scale(gray.shape[0])
    gray_up = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _, otsu = cv2.threshold(gray_up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    declut = remove_long_lines(otsu)
    declut = cv2.morphologyEx(declut, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    variants = [cv2.bitwise_not(declut), declut]
    # `scale` is returned alongside the data because every coordinate in it
    # (left/top/width/height, and the cx/cy derived from them) is in the
    # UPSCALED space — callers doing distance math against the original crop's
    # dimensions must scale those dimensions up to match.
    return best_ocr_variant(variants, "--psm 11"), scale
