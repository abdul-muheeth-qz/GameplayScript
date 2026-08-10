"""Step 3 over a run folder: the two OCR records in, a verdict out.

The standalone validator printed the word `Pass` and nothing else, which is enough for a
shell but not for a UI -- a verdict with no numbers behind it cannot be argued with. So
the result is a dict now, written to `validate.json` beside the frames it judged:

    { "verdict": "pass" | "fail" | "error", "expected_cash": "1155.76",
      "computed_cash": "1155.76", "difference": "0.00", "tolerance": "0.005",
      "record": "1175.76,20.00,40.00", "formula": "cash + win - bet",
      "model": "...", "inferred": ["win"], "message": "..." }

Money is carried as strings, because the whole point of reading it as Decimal is that it
stays exact, and putting it through a JSON float would undo that.

The division of labour from the original stands and is deliberate: the **model does the
arithmetic**, and Python does the comparison. Checking the model's sum against a Python
one would only be testing Python.
"""

from __future__ import annotations

import json
import logging
import os
from decimal import Decimal
from pathlib import Path

from .agent import compute_cash, endpoint_settings, format_record
from .records import FIELDS, RecordError, load_values

LOG = logging.getLogger("validate")

# Cash values are decimal currency; treat differences under half a cent as rounding noise
# rather than a real mismatch. Compared as Decimal, so the boundary sits exactly on half a
# cent instead of wherever binary float lands.
DEFAULT_TOLERANCE = Decimal("0.005")

FORMULA = "cash + win - bet"

RESULT_FILE = "validate.json"

# What the extract step writes inside a run folder, and what the standalone sample data
# in data/ is called. Both are accepted, so `python -m server.validate.cli server/validate/data`
# still works exactly as it did.
RUN_LAYOUT = ("extract/before.json", "extract/after.json")
LEGACY_LAYOUT = ("before_spin.json", "after_spin.json")


def arithmetic(record: str) -> str:
    """The record rendered as the sum it stands for: "1175.76 + 20.00 - 40.00"."""
    cash, win, bet = record.split(",")
    return f"{cash} + {win} - {bet}"


def find_records(folder: str | os.PathLike) -> tuple[Path, Path]:
    """The before and after record files in `folder`, whichever layout it uses."""
    folder = Path(folder)
    for before_name, after_name in (RUN_LAYOUT, LEGACY_LAYOUT):
        before, after = folder / before_name, folder / after_name
        if before.is_file() and after.is_file():
            return before, after
    raise RecordError(
        f"{folder} holds neither {'/'.join(RUN_LAYOUT)} nor {'/'.join(LEGACY_LAYOUT)} -- "
        f"run the extract step over this folder first"
    )


def validate_records(before: Path, after: Path, cfg: dict | None = None) -> dict:
    """Judge one spin from its two OCR records. Never raises: errors are a verdict."""
    cfg = cfg or {}
    tolerance = Decimal(str(cfg.get("validate", {}).get("tolerance", DEFAULT_TOLERANCE)))
    model = endpoint_settings(cfg)[0]
    result = {"verdict": "error", "expected_cash": None, "computed_cash": None,
              "difference": None, "tolerance": str(tolerance), "record": None,
              "formula": FORMULA, "model": model, "inferred": [], "message": ""}

    try:
        # Both files are read before the model is called, so a broken record fails in
        # milliseconds instead of after a round trip.
        after_values, after_inferred = load_values(after, ("cash",))
        before_values, before_inferred = load_values(before, FIELDS)
        actual_cash = after_values["cash"]
        record = format_record(before_values)
        result["inferred"] = sorted(set(before_inferred) | set(after_inferred))
        result["expected_cash"] = str(actual_cash)
        result["record"] = record

        computed_cash = compute_cash(record, cfg)
    except Exception as exc:
        result["message"] = str(exc)
        return result

    difference = computed_cash - actual_cash
    result["computed_cash"] = str(computed_cash)
    result["difference"] = str(difference)

    if abs(difference) < tolerance:
        result["verdict"] = "pass"
        result["message"] = (f"{arithmetic(record)} = {computed_cash}, "
                             f"which is the cash meter after the spin")
        return result

    # Full precision, plus the record the model was given: without those the two numbers
    # can print identically and prove nothing.
    result["verdict"] = "fail"
    result["message"] = (f"expected {actual_cash}, model computed {computed_cash} "
                         f"from record {record}")
    return result


def validate_run(run_dir: str, cfg: dict | None = None) -> dict:
    """Validate a capture run folder and write validate.json into it."""
    try:
        before, after = find_records(run_dir)
    except RecordError as exc:
        return {"verdict": "error", "message": str(exc), "formula": FORMULA,
                "inferred": [], "expected_cash": None, "computed_cash": None,
                "difference": None, "tolerance": None, "record": None, "model": None}

    result = validate_records(before, after, cfg)
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
