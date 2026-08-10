"""Step 2 -- read the meters off a captured frame.

Crops a captured frame to the CASH / WIN / BET meter bar and OCRs only that strip with
Tesseract. No fixed pixel coordinates: the crop comes from one of three methods --
horizontal bands, a normalized configured box, or OpenCV dark-panel detection -- chosen
in `slotocr/roi_config.py`, with no fallback from one to another.

    python -m server.extract.cli captured_files/<run_id>   # a whole run folder
    python -m server.extract.cli some/screenshot.png       # one image, JSON to stdout

`extract_frames(run_dir, cfg)` is what the server calls; the record it writes per frame
is what `validate` reads, keyed cash / win / bet.
"""

from .runner import extract_frames, read_frames
from .slotocr import RoiMethod, process_image

__all__ = ["extract_frames", "read_frames", "process_image", "RoiMethod"]
