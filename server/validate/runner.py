"""Stage 3 over a run folder: the two OCR records in, a verdict out.

Which frame each number comes from is the correctness question here, and it is one rule for both
cases -- cash and bet from `pre_spin`, cash and win from the **last** frame there is
(`win_collected` on a win, `spin_result` on a loss). So the win is always the second record's.
`pre_spin`'s WIN meter is not read at all: it holds the previous spin's win, and reading it
double-counts.

The cost, and it is real: on a win that means a third OCR pass over a meter `spin_result` already
read, and it does fail on one run on disk -- both `win` and `bet` null, so the win is taken as 0.00
and the verdict is a Fail off by exactly the win. That is an `extract` bug to fix there, not a
reason to move where this reads the win; the ROI crop for that frame is clean and legible.

The result goes to `validate.json` with money as strings, so it stays exact across JSON.
"""

from __future__ import annotations

import json
import logging
import os
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

from .. import frames
from ..extract.runner import EXTRACT_SUBDIR
from .ledger import FORMULA, TOLERANCE, judge, pad
from .records import CURRENT_FIELDS, PREVIOUS_FIELDS, RecordError, load_values

LOG = logging.getLogger("validate")

RESULT_FILE = "validate.json"


class Sources(NamedTuple):
    """The two record files a verdict is reached over, and the stages they name."""

    previous: Path
    current: Path
    stages: tuple[str, ...]


def find_records(folder: str | os.PathLike) -> Sources:
    """The pre-spin record and the last one there is, for a run folder."""
    extract_dir = Path(folder) / EXTRACT_SUBDIR
    previous = extract_dir / f"{frames.PRE_SPIN}.json"
    result = extract_dir / f"{frames.SPIN_RESULT}.json"
    collected = extract_dir / f"{frames.WIN_COLLECTED}.json"

    if not (previous.is_file() and result.is_file()):
        raise RecordError(
            f"{folder} has no {EXTRACT_SUBDIR}/{frames.PRE_SPIN}.json and "
            f"{EXTRACT_SUBDIR}/{frames.SPIN_RESULT}.json -- run the extract step over "
            f"this folder first")

    # A win parks the money on the collect offer, so the cash meter is only settled on the third
    # frame. Its absence means the spin did not win, and `spin_result` is then the final frame.
    if collected.is_file():
        return Sources(previous, collected,
                       (frames.PRE_SPIN, frames.WIN_COLLECTED))
    return Sources(previous, result, (frames.PRE_SPIN, frames.SPIN_RESULT))


def validate_records(sources: Sources, tolerance: Decimal = TOLERANCE) -> dict:
    """Judge one spin from its two records. Never raises: an error is a verdict."""
    result = {"verdict": "error", "expected_cash": None, "computed_cash": None,
              "difference": None, "tolerance": str(tolerance), "record": None,
              "formula": FORMULA, "inferred": [],
              "message": "", "stages": list(sources.stages),
              "sources": {"cash_and_bet": sources.previous.name,
                          "win": sources.current.name,
                          "final": sources.current.name}}

    try:
        # `previous` returns no inferences by construction: neither cash nor bet is in
        # `records.INFERABLE`, a blank one being a failed read rather than a zero.
        previous, _ = load_values(sources.previous, PREVIOUS_FIELDS)
        current, current_inferred = load_values(sources.current, CURRENT_FIELDS)

        # The ledger in the order the UI draws it, and padded so validate.json holds
        # digit-for-digit what was read off the meters -- `str(Decimal)` renders a JSON 2926.7 as
        # "2926.7", which reads as a different number from the 2926.70 on the glass.
        result["inferred"] = sorted(set(current_inferred))
        result["record"] = ",".join(pad(v) for v in
                                    (previous["cash"], current["win"], previous["bet"]))
        result["expected_cash"] = pad(current["cash"])

        verdict = judge(previous, current, tolerance)
    except Exception as exc:
        result["message"] = str(exc)
        return result

    # Two places on the way out, so a cash meter OCR'd as "2926.7" does not sit in the ledger as a
    # one-place number beside two-place ones.
    result["verdict"] = verdict.verdict
    result["computed_cash"] = f"{verdict.computed_cash:.2f}"
    result["difference"] = f"{verdict.difference:.2f}"
    result["message"] = verdict.working
    return result


def validate_run(run_dir: str, tolerance: Decimal = TOLERANCE) -> dict:
    """Validate a capture run folder and write validate.json into it."""
    try:
        sources = find_records(run_dir)
    except RecordError as exc:
        return {"verdict": "error", "message": str(exc), "formula": FORMULA,
                "inferred": [], "expected_cash": None, "computed_cash": None,
                "difference": None, "tolerance": None, "record": None,
                "sources": None, "stages": []}

    result = validate_records(sources, tolerance)
    path = os.path.join(run_dir, RESULT_FILE)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    LOG.info("wrote %s", path)
    return result


def read_result(run_dir: str) -> dict | None:
    """The verdict already reached for this run, or None if it hasn't been judged."""
    path = os.path.join(run_dir, RESULT_FILE)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)
