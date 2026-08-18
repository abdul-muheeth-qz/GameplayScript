"""Low-level OCR helpers: preprocessing for tesseract, and OCR over small localized crops.

Nothing here assumes fixed coordinates or a game layout.
"""
import re

import cv2
import numpy as np
import pytesseract
from pytesseract import Output


def token_conf(value):
    """One entry of an image_to_data "conf" column as a float. Tesseract reports -1 for a row
    carrying no text, and anything unparseable is reported the same way."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1.0


def token_confidences(data, require_text=False):
    """Confidences from an image_to_data dict, with tesseract's -1 placeholder rows dropped."""
    return [
        conf
        for conf, text in zip(map(token_conf, data["conf"]), data["text"])
        if conf >= 0 and (not require_text or text.strip())
    ]


def best_ocr_variant(variants, config):
    """The best of several preprocessing variants of the SAME image, scored on mean token
    confidence plus a small bonus for finding more tokens."""
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


# The one config string both sparse-text paths use, and what is NOT in it is the useful part.
# Each was measured against four bracketing ROI crops and then against the whole corpus; do not add
# one back without re-running both.
#
#   --dpi 300        The near miss. Best on the four crops (the only option reading both torn
#                    amounts whole) and a net loss over the corpus: two balances to null, one `bet`
#                    lost, and "$2,184.95" read as 184.95. Ledger 24/1 pass/fail -> 22/2.
#   dawgs off        Placebo -- not one token changed on any of the four crops.
#   --psm 6          Reads both torn amounts whole, but psm 11 still wins the tight crop, so it
#                    would have to be a second raced pass at double the cost. Start here if the
#                    tearing ever needs solving in the engine.
#   whitelist        Harmful here: it forces the classifier to the best ALLOWED character rather
#                    than dropping glyphs, so artwork becomes text. Already used where it is safe,
#                    on ocr_small_crop's tight single-line crops.
#   --oem 1, -l eng  Placebos on this install -- the tessdata is eng+osd and LSTM-only already.
SPARSE_TEXT_CONFIG = "--psm 11"


def preprocess_for_ocr(image):
    """An upscaled, high-contrast grayscale image for bright-text-on-dark slot UI. Whole-image
    preprocessing, used only by the last-resort fallback method."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Meter text is small relative to the screenshot.
    scale = 2
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # Local contrast, for glowing/gradient UI text.
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    gray = cv2.bilateralFilter(gray, 5, 50, 50)

    # Both polarities go to run_ocr_data, which lets tesseract's confidence pick.
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    return gray, binary, scale


def run_ocr_data(gray, binary):
    """Whichever preprocessing variant reads better -- no assumption of a fixed best."""
    return best_ocr_variant([gray, binary], SPARSE_TEXT_CONFIG)


def remove_long_lines(binary_text_white, frac=0.55):
    """Strip long thin border lines, preserving glyph strokes -- which are much shorter than the
    panel is wide."""
    w = binary_text_white.shape[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, int(w * frac)), 1))
    lines = cv2.morphologyEx(binary_text_white, cv2.MORPH_OPEN, kernel)
    return cv2.subtract(binary_text_white, lines)


SMALL_CROP_UPSCALE = 5


def ocr_small_crop(crop, psms, whitelist=None):
    """(text, confidence) for one panel's number or label region, trying each PSM in `psms` and
    keeping the most characters -- a stylized font can trip one segmentation mode and not another."""
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
    """6x for a thin merged meter bar (~28 px holding all three labels and values), 3x otherwise.

    At 3x-5x that bar's small BET label dissolves into garbage ("ee", "le") and the field is lost;
    at 6x it reads as "8ET", which label_similarity's lookalike handling recovers. Only genuinely
    short panels get the boost, so every other layout keeps the default it was tuned against.
    """
    return 6 if height <= 32 else 3


def panel_word_ocr(crop):
    """A panel crop's image_to_data dict, psm 11, best of both polarities."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    scale = _panel_ocr_scale(gray.shape[0])
    gray_up = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _, otsu = cv2.threshold(gray_up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    declut = remove_long_lines(otsu)
    declut = cv2.morphologyEx(declut, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    # Both polarities are needed: the target amount came back whole 41 times on each, a dead tie.
    variants = [cv2.bitwise_not(declut), declut]
    # `scale` rides along because every coordinate in the data is in the UPSCALED space -- a caller
    # doing distance math against the original crop must scale its dimensions to match.
    return best_ocr_variant(variants, SPARSE_TEXT_CONFIG), scale
