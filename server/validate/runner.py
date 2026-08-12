"""Step 3 over a run folder: the two OCR records in, a verdict out.

Which frame each number is read from is the correctness question in this module, and it is
one rule for both cases:

    previous   cash, bet   pre_spin        the balance it started from, and the wager
    current    cash, win   the last frame  what it ended at, and what it paid

The last frame is `win_collected` when the spin won and `spin_result` when it did not. So
the *win is always read from the second record*. `pre_spin`'s WIN meter is neither read nor
sent: it holds the previous spin's win, which the game leaves on display after paying it,
and reading it there double-counts (see `server.frames` and `records.PREVIOUS_FIELDS`).

    a loss:  pre_spin.cash - bet + 0.00              == spin_result.cash
    a win:   pre_spin.cash - bet + win_collected.win == win_collected.cash

Verified on run 2026-08-11_212236: `2909.60 - 1.00 + 18.10 = 2926.70`. A winning spin's
WIN meter reads the same on `spin_result` and `win_collected` -- the game holds it there
until the next spin clears it -- which is what lets one rule cover both.

**The cost of reading the win from the last frame**, and it is a real one: on a win that
means a third OCR pass over a meter `spin_result` had already read, and on run
2026-08-11_204202 that pass fails. `extract/win_collected.json` there has `win` and `bet`
both null, so the win is taken as 0.00 and the run reports Fail with a difference of exactly
-24.00. That is an `extract` bug and should be fixed there rather than by moving where this
reads the win: the ROI crop for that frame is clean and legible, and the three ROI methods
each read a different subset of it -- `configured` gets cash (at confidence 0.0) and nothing
else, `bands` gets win 24.00 at 95 and bet 1.00 at 93 but no cash, `dynamic` gets nothing.

The verdict, and every number under it, comes from the model: see `agent.Verdict`. Nothing
here adds these up or compares them. The result is written to `validate.json` beside the
frames it judged, with money as strings so it stays exact across the JSON boundary:

    { "verdict": "pass" | "fail" | "error", "expected_cash": "2926.70",
      "computed_cash": "2926.70", "difference": "0.00", "tolerance": "0.005",
      "record": "2909.60,18.10,1.00", "formula": "...", "model": "...",
      "inferred": ["win"], "message": "...", "sources": {...}, "stages": [...] }
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
from .agent import FORMULA, endpoint_settings, judge, pad
from .records import CURRENT_FIELDS, PREVIOUS_FIELDS, RecordError, load_values

LOG = logging.getLogger("validate")

# Cash values are decimal currency; treat differences under half a cent as rounding noise
# rather than a real mismatch. This is an *input* -- it goes into the prompt, and the model
# applies it. Carried as Decimal so the boundary sits exactly on half a cent instead of
# wherever binary float lands.
DEFAULT_TOLERANCE = Decimal("0.005")

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

    # A win parks the money on the collect offer, so the cash meter is only settled on the
    # third frame. Its absence means the spin did not win, and is information rather than
    # a failure -- `spin_result` is then the final frame.
    if collected.is_file():
        return Sources(previous, collected,
                       (frames.PRE_SPIN, frames.WIN_COLLECTED))
    return Sources(previous, result, (frames.PRE_SPIN, frames.SPIN_RESULT))


def validate_records(sources: Sources, cfg: dict | None = None) -> dict:
    """Judge one spin from its two records. Never raises: errors are a verdict."""
    cfg = cfg or {}
    tolerance = Decimal(str(cfg.get("validate", {}).get("tolerance", DEFAULT_TOLERANCE)))
    result = {"verdict": "error", "expected_cash": None, "computed_cash": None,
              "difference": None, "tolerance": str(tolerance), "record": None,
              "formula": FORMULA, "model": endpoint_settings(cfg)[0], "inferred": [],
              "message": "", "stages": list(sources.stages),
              "sources": {"cash_and_bet": sources.previous.name,
                          "win": sources.current.name,
                          "final": sources.current.name}}

    try:
        # Both files are read before the model is called, so a broken record fails in
        # milliseconds instead of after a round trip. `previous` returns no inferences by
        # construction: it is read for cash and bet, and neither of those is in
        # `records.INFERABLE` -- a blank one is a failed read, not a zero.
        previous, _ = load_values(sources.previous, PREVIOUS_FIELDS)
        current, current_inferred = load_values(sources.current, CURRENT_FIELDS)

        # The ledger the UI draws, in the order it draws it: the cash before, the win this
        # spin paid, the bet that was placed -- `win` being the *current* frame's, see the
        # module docstring. Padded, so validate.json holds digit-for-digit what the model
        # was shown: `str(Decimal)` alone renders a JSON 2926.7 as "2926.7", which reads
        # as a different number from the "2926.70" in the prompt.
        result["inferred"] = sorted(set(current_inferred))
        result["record"] = ",".join(pad(v) for v in
                                    (previous["cash"], current["win"], previous["bet"]))
        result["expected_cash"] = pad(current["cash"])

        verdict = judge(previous, current, tolerance, cfg)
    except Exception as exc:
        result["message"] = str(exc)
        return result

    # Formatted to two places on the way out, so the ledger reads as money rather than as
    # whatever JSON float the model happened to emit -- 2926.7, or 0.009999999999990905
    # for a difference of a penny. These are the model's numbers, not a check on them.
    result["verdict"] = verdict.verdict
    result["computed_cash"] = f"{verdict.computed_cash:.2f}"
    result["difference"] = f"{verdict.difference:.2f}"
    result["message"] = verdict.working
    return result


def validate_run(run_dir: str, cfg: dict | None = None) -> dict:
    """Validate a capture run folder and write validate.json into it."""
    try:
        sources = find_records(run_dir)
    except RecordError as exc:
        return {"verdict": "error", "message": str(exc), "formula": FORMULA,
                "inferred": [], "expected_cash": None, "computed_cash": None,
                "difference": None, "tolerance": None, "record": None, "model": None,
                "sources": None, "stages": []}

    result = validate_records(sources, cfg)
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
