"""Stage 2 over a run folder: the frames in, one JSON record each out, into `extract/`.

Two frames or three, and the difference is not a failure -- `win_collected` exists only when the
spin won, so the mandatory set is `frames.REQUIRED` and anything else present is extracted too.
One record per file, because validate reads exactly one spin per file.
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


# server.frames owns the names and the on-disk lookup, and capture writes against the same module.
frame_path = frames.find


def record_path(run_dir: str, name: str) -> str:
    return os.path.join(extract_dir(run_dir), f"{name}.json")


def extract_frames(run_dir: str, cfg: dict | None = None) -> dict:
    """OCR every frame this run captured, write their records, and return them all.

    A thread per frame, which really is about a third of the wall clock of three in turn: tesseract
    is an external process and pytesseract releases the GIL waiting on it.
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

    # Whatever is there, in order. A losing spin has no win_collected frame.
    present = frames.present(run_dir)
    with ThreadPoolExecutor(max_workers=len(present)) as pool:
        records = dict(zip(present, pool.map(
            lambda name: process_image(frames.find(run_dir, name),
                                       roi_dir=out_dir if save_crops else None, cfg=cfg),
            present)))

    for name, record in records.items():
        with open(record_path(run_dir, name), "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
        LOG.info("wrote %s", record_path(run_dir, name))

    return records


def read_frames(run_dir: str) -> dict | None:
    """Records already extracted for this run, or None if the step hasn't run.

    Judged on `frames.REQUIRED` alone -- waiting for a third record would report every losing spin
    as un-extracted forever.
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
