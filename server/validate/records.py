"""Read the OCR records the extract step wrote, as exact Decimals.

Two sets of meters are read per spin, and which file each comes from is `runner.find_records`:

    previous   cash, bet   -- pre_spin
    current    cash, win   -- the last frame captured

One rule beyond "parse the number": a **null win is taken as 0.00**. `extract` reports
`"value": null` for a meter it could not read *and* for a meter with nothing in it, and a
blank WIN box is the second one -- it is the correct reading of an empty meter, and it is
what every losing spin's result frame looks like. Erroring on it would mean an ordinary
spin could never be validated at all.

Cash and bet get no such treatment: a blank cash meter is not zero credits, it is a failed
read, and inferring a balance would turn an OCR failure into a verdict. Whatever was
assumed comes back in `inferred`, so the UI badges it and a wrong assumption stays visible
instead of hiding inside a Pass.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

# What is read from each frame. The extract step's slotocr.config.FIELD_LABELS emits
# exactly these keys.
#
# `win` is deliberately absent from PREVIOUS_FIELDS. `pre_spin`'s WIN meter holds the
# *previous* spin's win -- the game leaves a paid win on display until the next spin clears
# it -- so reading it here would double-count. A field nothing reads cannot be added into
# the sum by mistake. The frame the win comes from instead is the one whose WIN meter means
# this spin: the last frame there is (`runner.find_records`).
PREVIOUS_FIELDS = ("cash", "bet")
CURRENT_FIELDS = ("cash", "win")

# Fields an empty meter genuinely reads as zero. Only win: see the module docstring.
INFERABLE = {"win": Decimal("0.00")}


class RecordError(ValueError):
    """A record that cannot be validated, with the reason a person can act on."""


def load_values(path: Path, fields: tuple[str, ...]) -> tuple[dict[str, Decimal], list[str]]:
    """The named values as Decimals, plus the names of any that were inferred."""
    try:
        # utf-8-sig reads a BOM-prefixed file as well as a bare one, and
        # parse_float=Decimal keeps the cash values exact.
        record = json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_float=Decimal)
    except (OSError, ValueError) as exc:
        raise RecordError(f"{path}: {exc}") from exc

    if not isinstance(record, dict):
        raise RecordError(f"{path}: expected a record object, "
                          f"found {type(record).__name__}")

    values: dict[str, Decimal] = {}
    inferred: list[str] = []
    for field in fields:
        value, was_inferred = _value(path, record, field)
        values[field] = value
        if was_inferred:
            inferred.append(field)
    return values, inferred


def _value(path: Path, record: dict, field: str) -> tuple[Decimal, bool]:
    """Pull one numeric OCR value out of a record, or say what was wrong."""
    try:
        raw = record[field]["value"]
    except (KeyError, TypeError) as exc:
        if field in INFERABLE:
            return INFERABLE[field], True
        raise RecordError(f"{path}: no {field} value in the record") from exc

    # Checked here rather than downstream, where a null, a word or a NaN would blow up
    # mid-sum. bool is rejected explicitly, being a subclass of int.
    if not isinstance(raw, bool):
        try:
            value = Decimal(raw)
            if value.is_finite():
                return value, False
        except (InvalidOperation, TypeError, ValueError):
            pass

    if raw is None and field in INFERABLE:
        return INFERABLE[field], True

    raise RecordError(f"{path}: {field} value is not a number: {raw!r}")
