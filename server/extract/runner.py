"""Step 2 over a capture run folder: the frames in, one JSON record each out.

This is the whole hand-off between capture and validate, and it is the reason the OCR
half stopped printing its answer to stdout. A run folder after this has run:

    captured_files/<run_id>/
        pre_spin.png  spin_result.png      <- capture wrote these
        win_collected.png                  <- and this one too, if the spin won
        extract/pre_spin.json              <- one record object, not a list
        extract/spin_result.json
        extract/pre_spin_roi.png           <- what was actually sent to Tesseract
        ...

**Two frames or three**, and the difference is not a failure: `win_collected` only exists
when the spin won, because it is the frame taken after the win was collected on the glass
(see `server.frames` for why a win needs its own frame at all). So the mandatory set is
`frames.REQUIRED` and anything else present is extracted as well.

One record per file rather than the list `process_image` results used to be collected
into: the validator reads exactly one spin per file, and a list of two would have been a
hard error there. The list form is still accepted on the way in, for the sample data.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from .. import frames
from . import tesseract
from .slotocr import process_image

LOG = logging.getLogger("extract")

EXTRACT_SUBDIR = "extract"


def extract_dir(run_dir: str) -> str:
    return os.path.join(run_dir, EXTRACT_SUBDIR)


# The frame names and their on-disk lookup live in server.frames, which capture writes
# against too -- one definition of the contract, not two that can drift.
frame_path = frames.find


def record_path(run_dir: str, name: str) -> str:
    return os.path.join(extract_dir(run_dir), f"{name}.json")


def extract_frames(run_dir: str, cfg: dict | None = None, roi_method=None) -> dict:
    """OCR every frame this run captured, write their records, and return them all.

    The frames are independent, so they are OCR'd on a thread each -- Tesseract is an
    external process and pytesseract releases the GIL waiting on it, so this really is
    about a third of the wall clock of doing three in turn.

    `roi_method` is passed straight through to `process_image` and so to
    `slotocr.roi.locate_meter_roi`; None means whatever `roi_config.ROI_METHOD` says.
    Every frame gets the same method -- one read from a band and another read from a
    configured box would be two different measurements, and validate compares them
    against each other.
    """
    cfg = cfg or {}
    tesseract.configure(cfg)
    tesseract.check()

    save_crops = cfg.get("extract", {}).get("save_roi_crops", True)
    out_dir = extract_dir(run_dir)
    os.makedirs(out_dir, exist_ok=True)

    missing = [name for name in frames.REQUIRED if frames.find(run_dir, name) is None]
    if missing:
        raise FileNotFoundError(
            f"{run_dir} has no {' or '.join(missing)} frame -- run the capture step first, "
            f"or check that capture.format in config.json matches what is on disk"
        )

    # Whatever is actually there, in order. A losing spin has no win_collected frame, and
    # that is information rather than something to complain about.
    present = frames.present(run_dir)
    with ThreadPoolExecutor(max_workers=len(present)) as pool:
        records = dict(zip(present, pool.map(
            lambda name: process_image(frames.find(run_dir, name),
                                       roi_dir=out_dir if save_crops else None,
                                       roi_method=roi_method),
            present)))

    for name, record in records.items():
        with open(record_path(run_dir, name), "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
        LOG.info("wrote %s", record_path(run_dir, name))

    return records


def read_frames(run_dir: str) -> dict | None:
    """Records already extracted for this run, or None if the step hasn't run.

    Judged on `frames.REQUIRED` alone: a losing spin is fully extracted with two records,
    so waiting for a third would report every loss as un-extracted forever.
    """
    records = {}
    for name in frames.ORDER:
        path = record_path(run_dir, name)
        if os.path.isfile(path):
            with open(path, encoding="utf-8-sig") as fh:
                records[name] = json.load(fh)
        elif name in frames.REQUIRED:
            return None
    return records or None
