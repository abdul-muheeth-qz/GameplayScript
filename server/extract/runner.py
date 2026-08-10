"""Step 2 over a capture run folder: the two frames in, two JSON files out.

This is the whole hand-off between capture and validate, and it is the reason the OCR
half stopped printing its answer to stdout. A run folder after this has run:

    captured_files/<run_id>/
        before.png  after.png              <- capture wrote these
        extract/before.json                <- one record object, not a list
        extract/after.json
        extract/before_roi.png             <- what was actually sent to Tesseract
        extract/after_roi.png

One record per file rather than the list `process_image` results used to be collected
into: the validator reads exactly one spin per file, and a list of two would have been a
hard error there. The list form is still accepted on the way in, for the sample data.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from . import tesseract
from .slotocr import process_image

LOG = logging.getLogger("extract")

# The two frames spin.py writes, and the names their records take. Keyed by the stage
# name so a caller can ask for one frame without knowing the file extension.
FRAMES = ("before", "after")

EXTRACT_SUBDIR = "extract"


def extract_dir(run_dir: str) -> str:
    return os.path.join(run_dir, EXTRACT_SUBDIR)


def frame_path(run_dir: str, name: str) -> str | None:
    """The captured frame for `name`, whatever image format capture was set to."""
    for ext in ("png", "jpg", "jpeg", "bmp", "webp"):
        path = os.path.join(run_dir, f"{name}.{ext}")
        if os.path.isfile(path):
            return path
    return None


def record_path(run_dir: str, name: str) -> str:
    return os.path.join(extract_dir(run_dir), f"{name}.json")


def extract_frames(run_dir: str, cfg: dict | None = None) -> dict:
    """OCR before and after, write their records, and return both.

    The two frames are independent, so they are OCR'd on two threads -- Tesseract is an
    external process and pytesseract releases the GIL waiting on it, so this really is
    about half the wall clock of doing them in turn.
    """
    cfg = cfg or {}
    tesseract.configure(cfg)
    tesseract.check()

    save_crops = cfg.get("extract", {}).get("save_roi_crops", True)
    out_dir = extract_dir(run_dir)
    os.makedirs(out_dir, exist_ok=True)

    missing = [name for name in FRAMES if frame_path(run_dir, name) is None]
    if missing:
        raise FileNotFoundError(
            f"{run_dir} has no {' or '.join(missing)} frame -- run the capture step first, "
            f"or check that capture.format in config.json matches what is on disk"
        )

    with ThreadPoolExecutor(max_workers=len(FRAMES)) as pool:
        records = dict(zip(FRAMES, pool.map(
            lambda name: process_image(frame_path(run_dir, name),
                                       roi_dir=out_dir if save_crops else None),
            FRAMES)))

    for name, record in records.items():
        with open(record_path(run_dir, name), "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
        LOG.info("wrote %s", record_path(run_dir, name))

    return records


def read_frames(run_dir: str) -> dict | None:
    """Records already extracted for this run, or None if the step hasn't run."""
    records = {}
    for name in FRAMES:
        path = record_path(run_dir, name)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8-sig") as fh:
            records[name] = json.load(fh)
    return records
