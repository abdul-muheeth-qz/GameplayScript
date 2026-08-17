"""Stage 2 -- read the meters off a captured frame.

Crops to the CASH / WIN / BET strip by the active game's normalized `meter_roi` and OCRs only that,
with nothing behind a box that misses. `extract_frames(run_dir, cfg)` is what the server calls, and
the per-frame record it writes is what `validate` reads, keyed cash / win / bet.
"""

from .runner import extract_frames, read_frames
from .slotocr import process_image

__all__ = ["extract_frames", "read_frames", "process_image"]
