"""Validate one slot spin.

    python -m server.validate.cli server/captured_files/2026-08-11_212236
    python -m server.validate.cli server/captured_files/2026-08-11_212236 --json

The argument is a capture run folder that the extract step has already been run over, so
that it holds `extract/pre_spin.json`, `extract/spin_result.json` and -- if the spin won --
`extract/win_collected.json`.

Prints Pass or Fail, and the working behind it on stderr.
"""

import argparse
import json
import sys
from pathlib import Path

from .runner import RESULT_FILE, find_records, validate_records, validate_run

# A Fail is a verdict about the spin. An error means no verdict was reached at all --
# keeping the two apart is what lets a test runner tell them apart.
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_ERROR = 2

EXIT_FOR = {"pass": EXIT_PASS, "fail": EXIT_FAIL, "error": EXIT_ERROR}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.validate.cli", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path,
                        help="a capture run folder the extract step has been run over")
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

    # No config is read here at all: the tolerance lives in `ledger.TOLERANCE`, beside the
    # comparison it governs, so this stage runs against a checkout with no config.json.
    if args.write:
        result = validate_run(str(args.folder))
    else:
        try:
            sources = find_records(args.folder)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        result = validate_records(sources)

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
