"""
slotocr — dynamically extracts Balance/Cash/Credit, Win, and Bet meter
values from slot-game screenshots using OpenCV + Tesseract OCR.

No fixed coordinates are assumed: the meter strip is cropped out by a
normalized box -- the active game's `games.<exe>.meter_roi` in
game_config.json, one box and nothing behind it -- and the crop is then OCR'd.
See slotocr.pipeline.process_image for the full per-image flow and slotocr.roi
for where the box comes from.
"""
from .pipeline import process_image

__all__ = ["process_image"]
