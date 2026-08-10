"""Validate one slot spin.

    python -m server.validate.cli captured_files/2026-08-07_141726   # a capture run folder
    python -m server.validate.cli server/validate/data               # the sample records

The argument is the folder holding the two OCR records -- either the run-folder layout
the extract step writes (extract/before.json, extract/after.json) or the standalone one
(before_spin.json, after_spin.json).

Prints Pass or Fail, and the arithmetic behind it on stderr.
"""

import argparse
import json
import sys
from pathlib import Path

from ..settings import load_config

from .runner import RESULT_FILE, find_records, validate_records, validate_run

# A Fail is a verdict about the spin. An error means no verdict was reached at all --
# keeping the two apart is what lets a test runner tell them apart.
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_ERROR = 2

EXIT_FOR = {"pass": EXIT_PASS, "fail": EXIT_FAIL, "error": EXIT_ERROR}

# Anchored on this file, not the current working directory, so the default works
# whether this is run from the repository root or from anywhere else.
DEFAULT_FOLDER = Path(__file__).resolve().parent / "data"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.validate.cli", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", nargs="?", default=DEFAULT_FOLDER, type=Path,
                        help="folder holding the two OCR records (default: the sample "
                             "records in server/validate/data)")
    parser.add_argument("--config", default=None,
                        help="path to config.json (default: the one at the repo root)")
    parser.add_argument("--json", action="store_true",
                        help="print the whole verdict object instead of one word")
    parser.add_argument("--write", action="store_true",
                        help=f"also write {RESULT_FILE} into the folder")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if not args.folder.is_dir():
        print(f"Folder not found: {args.folder}", file=sys.stderr)
        return EXIT_ERROR

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        # The endpoint has defaults and environment overrides behind it, so a missing
        # config is a warning rather than the end of the run.
        print(f"warning: could not read config ({exc}); using defaults", file=sys.stderr)
        cfg = {}

    if args.write:
        result = validate_run(str(args.folder), cfg)
    else:
        try:
            before, after = find_records(args.folder)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        result = validate_records(before, after, cfg)

    if args.json:
        print(json.dumps(result, indent=2))
        return EXIT_FOR[result["verdict"]]

    if result["verdict"] == "error":
        print(f"Error: {result['message']}", file=sys.stderr)
        return EXIT_ERROR

    if result["message"]:
        print(result["message"], file=sys.stderr)
    if result["inferred"]:
        print(f"(assumed {', '.join(result['inferred'])} = 0.00; the meter read blank)",
              file=sys.stderr)
    print("Pass" if result["verdict"] == "pass" else "Fail")
    return EXIT_FOR[result["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
