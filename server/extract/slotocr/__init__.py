"""
slotocr — dynamically extracts Balance/Cash/Credit, Win, and Bet meter
values from slot-game screenshots using OpenCV + Tesseract OCR.

No fixed coordinates are assumed. Meter panels are located by their visual
properties (dark, flat-colored UI panels vs. busy game artwork), cropped,
and OCR'd individually — see slotocr.pipeline.process_image for the full
per-image flow.
"""
from .pipeline import process_image

__all__ = ["process_image"]
