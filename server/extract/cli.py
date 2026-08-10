r"""Read the slot meters off one or more screenshots.

    python -m server.extract.cli captured_files/2026-08-07_141726   # a capture run folder
    python -m server.extract.cli server/extract/Images/after.png    # one image
    python -m server.extract.cli some/folder/ --out results/        # a folder of images

Given a **capture run folder** -- one holding before.png and after.png -- this does the
real step 2: it writes extract/before.json and extract/after.json inside that folder,
which is exactly what `python -m server.validate.cli <run folder>` then reads. Given
loose images it falls back to the older behaviour of printing a JSON list to stdout,
which is how the sample images in Images/ are still checked.

Requires the Tesseract OCR engine. See extract/tesseract.py for how it is found.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys

from ..settings import load_config

from . import tesseract
from .runner import FRAMES, extract_frames, frame_path
from .slotocr import process_image

LOG = logging.getLogger("extract")

HERE = os.path.dirname(os.path.abspath(__file__))

# Used when no path is given at all, so `python -m server.extract.cli` still does something.
DEFAULT_IMAGE_PATH = os.environ.get(
    "SLOTOCR_DEFAULT_IMAGE", os.path.join(HERE, "Images", "after.png")
)

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")

EXIT_OK, EXIT_ERROR = 0, 1


def is_run_folder(path: str) -> bool:
    """True for a folder the capture step wrote.

    Keyed on spin.json rather than on "has a before and an after in it": the sample
    images in Images/ include a before.png and an after.png too, and treating that
    folder as a run would quietly write records into it instead of printing them.
    """
    return (os.path.isdir(path)
            and os.path.isfile(os.path.join(path, "spin.json"))
            and all(frame_path(path, name) for name in FRAMES))


def gather_image_paths(args):
    """Expand arguments (files and/or directories) into a de-duplicated, ordered list.

    Extension matching is case-insensitive (a `.PNG` screenshot is still a screenshot),
    and directory names are glob-escaped so a folder containing glob metacharacters --
    `[`, `]`, `?`, `*` -- is not silently expanded into nothing.
    """
    paths = []
    seen = set()

    def add(p):
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            paths.append(p)

    for a in args:
        if os.path.isdir(a):
            for p in sorted(glob.glob(os.path.join(glob.escape(a), "*"))):
                if os.path.isfile(p) and p.lower().endswith(IMAGE_EXTENSIONS):
                    add(p)
        elif os.path.isfile(a):
            add(a)
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.extract.cli", description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*",
                        help="a capture run folder, or images and folders of images")
    parser.add_argument("--out", metavar="DIR",
                        help="write one <name>.json per image here, and the ROI crops "
                             "alongside them (default for loose images: print only)")
    parser.add_argument("--config", default=None,
                        help="path to config.json (default: the one at the repo root)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(message)s", stream=sys.stderr)

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        # Not fatal: the OCR half only reads config for the tesseract path, which has
        # an environment variable and a default behind it.
        LOG.warning("could not read config (%s); carrying on with defaults", exc)
        cfg = {}

    paths = args.paths or [DEFAULT_IMAGE_PATH]

    # A run folder is the real step-2 path: write the records where validate looks.
    run_folders = [p for p in paths if is_run_folder(p)]
    if run_folders:
        if len(run_folders) != len(paths):
            LOG.error("error: mixing capture run folders with loose images is ambiguous "
                      "-- run them separately")
            return EXIT_ERROR
        failed = False
        for folder in run_folders:
            try:
                records = extract_frames(folder, cfg)
            except Exception as exc:
                LOG.error("error: %s: %s", folder, exc)
                failed = True
                continue
            print(json.dumps(records, indent=2))
        return EXIT_ERROR if failed else EXIT_OK

    # Otherwise: loose images, printed as a list the way this tool always has.
    tesseract.configure(cfg)
    LOG.info("tesseract_cmd = %s", tesseract.pytesseract.pytesseract.tesseract_cmd)

    image_paths = gather_image_paths(paths)
    if not image_paths:
        LOG.error("error: no images found at %s -- check the path, filename and extension",
                  ", ".join(repr(p) for p in paths))
        return EXIT_ERROR

    if args.out:
        os.makedirs(args.out, exist_ok=True)

    results = []
    for path in image_paths:
        try:
            record = process_image(path, roi_dir=args.out)
        except Exception as exc:
            LOG.exception("error processing %s", path)
            record = {"image": os.path.basename(path), "error": str(exc)}
        results.append(record)
        if args.out:
            name = os.path.splitext(os.path.basename(path))[0]
            with open(os.path.join(args.out, f"{name}.json"), "w", encoding="utf-8") as fh:
                json.dump(record, fh, indent=2)

    print(json.dumps(results, indent=2))
    return EXIT_ERROR if any("error" in r for r in results) else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
