"""
slotocr — dynamically extracts Balance/Cash/Credit, Win, and Bet meter
values from slot-game screenshots using OpenCV + Tesseract OCR.

No fixed coordinates are assumed: the meter strip is cropped out by one of
three methods — horizontal bands, a normalized configured box, or dark-panel
detection — selected in slotocr.roi_config, and the crop is then OCR'd. See
slotocr.pipeline.process_image for the full per-image flow and slotocr.roi for
the three methods.
"""
from .pipeline import process_image
from .roi_config import RoiMethod

__all__ = ["process_image", "RoiMethod"]
