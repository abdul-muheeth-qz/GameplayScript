"""Stage 4 from the command line, over a run folder or a loose image.

    python -m server.payline.cli                                    the newest usable capture
    python -m server.payline.cli captured_files/<run>               one particular run
    python -m server.payline.cli captured_files/<run> --tiles-only  just crop and cut
    python -m server.payline.cli captured_files/<run> --json        the whole record
    python -m server.payline.cli --image <path>                     a loose image, forced
    python -m server.payline.cli --profile <path> X0 Y0 X1 Y1       measure a new game

With no run folder it reads the newest capture that actually holds a `spin_result` -- the same
default `POST /api/payline` uses, so the command line and the page agree about which spin "the
latest one" is. `--image` is the way to force a particular file regardless of what is on disk;
`payline.image` in config.json is only a fallback for when no capture has a frame.

Exit codes follow the house rule that a test runner needs one distinction: `0` some line
pays, `1` an error, `2` nothing pays. That is the same shape as `validate.cli`'s pass / error
/ no-verdict and for the same reason -- the interesting outcome has to be distinguishable
from the failure to reach one.

`--profile` is the aid for adding a game: give it a rough box around the reels and it reports
where the reel background actually starts and stops and where the gutters are, for a person to
turn into a `geometry.GAMES` block. It does not guess the box -- see `tiles.profile` for the
two auto-detection approaches that were written, measured against this cabinet's frames, and
thrown away.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile

from ..settings import load_config
from . import report
from .geometry import PaylineError
from .runner import build_tiles_for, validate_paylines

EXIT_PAYS, EXIT_ERROR, EXIT_NO_PAY = 0, 1, 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", nargs="?",
                        help="a run folder under captured_files/ (default: the newest one "
                             "holding a spin_result frame)")
    parser.add_argument("--image", help="validate this image instead of a run's frame")
    parser.add_argument("--profile", nargs=5, metavar=("IMAGE", "X0", "Y0", "X1", "Y1"),
                        help="report reel-background density inside a rough box, and exit")
    parser.add_argument("--tiles-only", action="store_true",
                        help="crop and cut the tiles, then stop")
    parser.add_argument("--json", action="store_true", help="print the whole record")
    parser.add_argument("--config", help="a config.json other than the repository's")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"cannot read config.json: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.profile:
        from .tiles import profile

        image, *box = args.profile
        try:
            result = profile(image, *(int(v) for v in box))
        except (PaylineError, ValueError) as exc:
            print(exc, file=sys.stderr)
            return EXIT_ERROR
        # The two density arrays are hundreds of numbers each and only useful when reading
        # an edge by hand, so they stay out of the summary unless asked for.
        dense = {k: result.pop(k) for k in ("row_density", "col_density")}
        print(json.dumps(result, indent=2))
        if args.verbose:
            print(json.dumps(dense, indent=2))
        return EXIT_PAYS

    # A loose image is judged in a scratch folder, so the run-folder contract holds for it
    # too and nothing has to special-case "there is no run".
    temp = None
    if args.image:
        if not os.path.isfile(args.image):
            print(f"no such image: {args.image}", file=sys.stderr)
            return EXIT_ERROR
        temp = tempfile.mkdtemp(prefix="payline-")
        run_dir = temp
        cfg.setdefault("payline", {})["image"] = os.path.abspath(args.image)
    elif args.run_dir:
        run_dir = args.run_dir
        if not os.path.isdir(run_dir):
            print(f"no such run folder: {run_dir}", file=sys.stderr)
            return EXIT_ERROR
    else:
        # The same default the API uses, so "the latest capture" means one thing across both.
        from .. import frames as frame_names
        from .. import runs

        latest = runs.latest(cfg, frame=frame_names.SPIN_RESULT)
        if not latest:
            print(f"no capture under {runs.captures_dir(cfg)} holds a "
                  f"{frame_names.SPIN_RESULT} frame, so there is nothing to read the reels "
                  f"off. Name a run folder, pass --image, or run the capture step first.",
                  file=sys.stderr)
            return EXIT_ERROR
        run_dir = runs.run_dir(cfg, latest)
        print(f"reading {latest}, the newest capture with a {frame_names.SPIN_RESULT} frame",
              file=sys.stderr)

    try:
        if args.tiles_only:
            info = build_tiles_for(run_dir, cfg)
            print(json.dumps(info, indent=2) if args.json else
                  f"{info['cells']} tiles of {info['tile_size']} from a "
                  f"{info['reels_size']} reel window ({info['image_source']})\n"
                  f"  check {os.path.join(run_dir, 'payline', 'tiles', 'contact_sheet.png')}")
            return EXIT_PAYS

        record = validate_paylines(run_dir, cfg)
    except PaylineError as exc:
        print(exc, file=sys.stderr)
        return EXIT_ERROR
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return EXIT_ERROR
    finally:
        if temp and not args.json:
            print(f"(artefacts in {temp})", file=sys.stderr)

    if args.json:
        print(json.dumps(record, indent=2))
    else:
        report.print_results(record)

    return EXIT_PAYS if record["lines_paying"] else EXIT_NO_PAY


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(EXIT_ERROR)
