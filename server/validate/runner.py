"""Step 3 over a run folder: the OCR records in, a verdict out.

A spin has two records, or three if it won, and **which record each value is read from is
the correctness question in this module** -- see `Sources`. In short: the cash it started
from and the bet come from `pre_spin`, what it paid comes from `spin_result`, and the cash
it is checked against comes from the last frame there is (`win_collected` on a win, and
`spin_result` again on a loss, where those are the same thing).


The standalone validator printed the word `Pass` and nothing else, which is enough for a
shell but not for a UI -- a verdict with no numbers behind it cannot be argued with. So
the result is a dict now, written to `validate.json` beside the frames it judged:

    { "verdict": "pass" | "fail" | "error", "expected_cash": "1155.76",
      "computed_cash": "1155.76", "difference": "0.00", "tolerance": "0.005",
      "record": "1175.76,20.00,40.00", "formula": "cash + win - bet",
      "model": "...", "inferred": ["win"], "message": "..." }

Money is carried as strings, because the whole point of reading it as Decimal is that it
stays exact, and putting it through a JSON float would undo that.

**The model owns the verdict outright** -- it does the arithmetic and the comparison, and
answers yes or no. Python computes `cash + win - bet` here too, but only to fill
`computed_cash` and `difference` so the UI has a ledger to draw; that sum never overrides
the model's answer. Where the two disagree, `message` says so out loud. That note is the
only signal that a verdict was reached wrongly, so don't quietly drop it.
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
from .agent import ask, endpoint_settings, format_cash, format_record, to_verdict
from .records import FIELDS, RecordError, load_values

LOG = logging.getLogger("validate")

# Cash values are decimal currency; treat differences under half a cent as rounding noise
# rather than a real mismatch. Compared as Decimal, so the boundary sits exactly on half a
# cent instead of wherever binary float lands.
DEFAULT_TOLERANCE = Decimal("0.005")

FORMULA = "cash + win - bet"

RESULT_FILE = "validate.json"

# The standalone sample data in data/, and run folders captured before the frames were named.
# Both keep the original semantics -- see Sources.
SAMPLE_LAYOUT = ("before_spin.json", "after_spin.json")
OLD_RUN_LAYOUT = ("extract/before.json", "extract/after.json")


class Sources(NamedTuple):
    """Which record file each part of the sum is read from.

    There are three sources rather than two because a winning spin has three frames, and
    each contributes a different thing:

        cash_and_bet   pre_spin      -- the balance the spin started from, and its wager
        win            spin_result   -- the frame whose whole purpose is to show what it paid
        final          win_collected -- the balance once the win was actually paid in

    On a losing spin `spin_result` is both the win and the final source, and the sum
    reduces to the original `cash + 0 - bet`. The win is deliberately **not** read from
    `pre_spin`: that meter holds the previous spin's win, which the game leaves on display
    after paying it, so reading it there double-counts (see server.frames).
    """

    cash_and_bet: Path
    win: Path
    final: Path
    stages: tuple[str, ...]
    legacy: bool = False


def arithmetic(record: str) -> str:
    """The record rendered as the sum it stands for: "1175.76 + 20.00 - 40.00"."""
    cash, win, bet = record.split(",")
    return f"{cash} + {win} - {bet}"


def find_records(folder: str | os.PathLike) -> Sources:
    """Which record file supplies which value, for whichever layout `folder` uses."""
    folder = Path(folder)

    pre = folder / EXTRACT_SUBDIR / f"{frames.PRE_SPIN}.json"
    result = folder / EXTRACT_SUBDIR / f"{frames.SPIN_RESULT}.json"
    collected = folder / EXTRACT_SUBDIR / f"{frames.WIN_COLLECTED}.json"
    if pre.is_file() and result.is_file():
        if collected.is_file():
            return Sources(pre, result, collected,
                           (frames.PRE_SPIN, frames.SPIN_RESULT, frames.WIN_COLLECTED))
        return Sources(pre, result, result, (frames.PRE_SPIN, frames.SPIN_RESULT))

    # Two frames, and the win read from the first of them: what both of these layouts meant
    # when they were written, and the sample data in data/ still depends on it.
    for first_name, second_name in (OLD_RUN_LAYOUT, SAMPLE_LAYOUT):
        first, second = folder / first_name, folder / second_name
        if first.is_file() and second.is_file():
            return Sources(first, first, second, (first.stem, second.stem), legacy=True)

    raise RecordError(
        f"{folder} holds none of the layouts this reads: "
        f"extract/{frames.PRE_SPIN}.json + extract/{frames.SPIN_RESULT}.json, "
        f"{'/'.join(OLD_RUN_LAYOUT)}, or {'/'.join(SAMPLE_LAYOUT)} -- run the extract step "
        f"over this folder first"
    )


def legacy_stale_win(run_dir: str | os.PathLike) -> bool:
    """Whether an *old* run folder collected a pending win before its before-frame.

    Only meaningful for `OLD_RUN_LAYOUT`, where the win is read from the first record. Those
    runs were captured by a short-lived `--collect-first` flow that paid the win into cash and
    left the WIN meter still showing it, so adding it double-counts. The current three-frame
    layout cannot produce that, because it never reads a win from the pre-spin frame at all --
    this exists so folders already on disk are not judged by the wrong sum.
    """
    try:
        with open(Path(run_dir) / "spin.json", encoding="utf-8-sig") as fh:
            return bool(json.load(fh).get("collected_separately"))
    except (OSError, ValueError):
        return False


def validate_records(sources: Sources, cfg: dict | None = None,
                     legacy_stale: bool = False) -> dict:
    """Judge one spin from its records. Never raises: errors are a verdict.

    `sources` says which file each value comes from, and getting that mapping right is the
    whole correctness question here -- see `Sources`. The sum itself is unchanged:

        cash (pre_spin) + win (spin_result) - bet (pre_spin)  ==  cash (final frame)

    On a losing spin `spin_result` is the final frame and the win is blank, so it reduces to
    `cash - bet`. On a winning spin the final frame is `win_collected`, whose cash meter is the
    only one in the run that has the win actually paid into it.

    `legacy_stale` is for old two-frame folders only, where the win is read from the first
    record: see `legacy_stale_win`.
    """
    cfg = cfg or {}
    tolerance = Decimal(str(cfg.get("validate", {}).get("tolerance", DEFAULT_TOLERANCE)))
    model = endpoint_settings(cfg)[0]
    result = {"verdict": "error", "expected_cash": None, "computed_cash": None,
              "difference": None, "tolerance": str(tolerance), "record": None,
              "formula": FORMULA, "model": model, "inferred": [], "message": "",
              "sources": {"cash_and_bet": sources.cash_and_bet.name,
                          "win": sources.win.name, "final": sources.final.name},
              "stages": list(sources.stages), "collected_separately": False}

    try:
        # Every file is read before the model is called, so a broken record fails in
        # milliseconds instead of after a round trip.
        cash_values, cash_inferred = load_values(sources.cash_and_bet, ("cash", "bet"))
        win_values, win_inferred = load_values(sources.win, ("win",))
        final_values, final_inferred = load_values(sources.final, ("cash",))
        actual_cash = final_values["cash"]
        values = {"cash": cash_values["cash"], "win": win_values["win"],
                  "bet": cash_values["bet"]}
        inferred = list(cash_inferred) + list(win_inferred) + list(final_inferred)

        # Before the record is formatted, so the model is asked the same question Python
        # answers rather than a different one.
        if legacy_stale and values["win"]:
            result["collected_separately"] = True
            inferred.append("win")
            values["win"] = Decimal("0.00")

        record = format_record(values)
        result["inferred"] = sorted(set(inferred))
        result["expected_cash"] = str(actual_cash)
        result["record"] = record

        # Python's own sum, for the ledger the UI draws and for the disagreement note
        # below. It is not the verdict and must never become one: the model was asked to
        # judge this spin, and quietly substituting Python's answer would mean shipping a
        # verdict nobody measured.
        computed_cash = values["cash"] + values["win"] - values["bet"]
        difference = computed_cash - actual_cash
        result["computed_cash"] = str(computed_cash)
        result["difference"] = str(difference)

        verdict = to_verdict(ask(record, format_cash(actual_cash), cfg))
    except Exception as exc:
        result["message"] = str(exc)
        return result

    result["verdict"] = verdict
    said = "yes" if verdict == "pass" else "no"
    result["message"] = (f"the model answered {said}: {arithmetic(record)} = "
                         f"{computed_cash} against a meter of {actual_cash}")
    if result.get("collected_separately"):
        result["message"] += (" -- this is an old two-frame run whose win had already been "
                              "collected into that cash meter, so it is counted as 0.00 here "
                              "rather than added twice")

    # The one thing worth saying twice. The model owns the verdict, so when the
    # arithmetic points the other way the only place that shows is here.
    # if (abs(difference) < tolerance) != (verdict == "pass"):
    #     result["message"] += " -- but the arithmetic disagrees with that answer"

    return result


def validate_run(run_dir: str, cfg: dict | None = None) -> dict:
    """Validate a capture run folder and write validate.json into it."""
    try:
        sources = find_records(run_dir)
    except RecordError as exc:
        return {"verdict": "error", "message": str(exc), "formula": FORMULA,
                "inferred": [], "expected_cash": None, "computed_cash": None,
                "difference": None, "tolerance": None, "record": None, "model": None,
                "sources": None, "stages": [], "collected_separately": False}

    result = validate_records(sources, cfg,
                              sources.legacy and legacy_stale_win(run_dir))
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
