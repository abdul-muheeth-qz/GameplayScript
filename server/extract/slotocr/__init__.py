"""Cash / win / bet meter values off a screenshot, with OpenCV and Tesseract.

No fixed coordinates: the strip is cropped by the active game's `meter_roi`, one box with nothing
behind it, and the crop is OCR'd. `pipeline.process_image` is the per-image flow.
"""
from .pipeline import process_image

__all__ = ["process_image"]
