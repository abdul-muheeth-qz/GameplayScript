"""Step 2 -- read the meters off a captured frame.

Locates the CASH / WIN / BET meter bar with OpenCV, crops to it, and OCRs only that
strip with Tesseract. No fixed pixel coordinates: the configured region is a normalized
box, and a dynamic dark-panel detector takes over when it misses.

    python -m server.extract.cli captured_files/<run_id>   # a whole run folder
    python -m server.extract.cli some/screenshot.png       # one image, JSON to stdout

`extract_frames(run_dir, cfg)` is what the server calls; the record it writes per frame
is what `validate` reads, keyed cash / win / bet.
"""

from .runner import extract_frames, read_frames
from .slotocr import process_image

__all__ = ["extract_frames", "read_frames", "process_image"]
