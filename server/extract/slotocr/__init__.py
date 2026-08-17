"""
slotocr — dynamically extracts Balance/Cash/Credit, Win, and Bet meter
values from slot-game screenshots using OpenCV + Tesseract OCR.

No fixed coordinates are assumed: the meter strip is cropped out by a
normalized box, one per game, held in game_config.json's `games.<exe>.meter_roi`
-- every game's box is raced against the screenshot, not just the active
game's, and the crop is then OCR'd. See slotocr.pipeline.process_image for the
full per-image flow and slotocr.roi for how the box is chosen.
"""
from .pipeline import process_image

__all__ = ["process_image"]
