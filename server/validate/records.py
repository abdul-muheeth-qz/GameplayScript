"""Read the OCR records the extract step wrote, as exact Decimals.

Split out of what used to be main.py so the server can reuse it. One rule was added
here that the standalone validator did not have: a **null win** is taken as 0.00.

That is not leniency. `extract` reports `"value": null` for a meter it could not read
*and* for a meter with nothing in it, and a blank WIN box before a spin is the second
one -- it is the correct reading of an empty meter, and it is what most before-frames
look like. Erroring on it meant an ordinary spin could never be validated at all. Cash
and bet get no such treatment: a blank cash meter is not zero credits, it is a failed
read, and inferring anything there would turn an OCR failure into a verdict.

Which values were assumed rather than read comes back in `inferred`, so the UI can
badge them and a wrong assumption stays visible instead of hiding inside a Pass.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

# The record's fields, in prompt order. agent.py's FIELDS is derived from this, and the
# extract step's slotocr.config.FIELD_LABELS emits exactly these keys.
FIELDS = ("cash", "win", "bet")

# Fields an empty meter genuinely reads as zero. Only win: see the module docstring.
INFERABLE = {"win": Decimal("0.00")}


class RecordError(ValueError):
    """A record that cannot be validated, with the reason a person can act on."""


def load_record(path: Path) -> dict:
    """One OCR record from a file, accepting the object or a one-element list form."""
    try:
        # utf-8-sig reads a BOM-prefixed export as well as a bare one, and
        # parse_float=Decimal keeps the cash values exact.
        text = Path(path).read_text(encoding="utf-8-sig")
        record = json.loads(text, parse_float=Decimal)
    except (OSError, ValueError) as exc:
        raise RecordError(f"{path}: {exc}") from exc

    if isinstance(record, list):
        # One spin per file. Silently keeping only the first record would report Pass
        # for a file whose later records were never looked at.
        if len(record) != 1:
            raise RecordError(f"{path}: expected 1 record, found {len(record)}")
        record = record[0]

    if not isinstance(record, dict):
        raise RecordError(
            f"{path}: expected a record object, found {type(record).__name__}")
    return record


def load_values(path: Path, fields: tuple[str, ...]) -> tuple[dict[str, Decimal], list[str]]:
    """The named values as Decimals, plus the names of any that were inferred."""
    record = load_record(path)
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

    # Checked here because this value is compared against the model's answer, where a
    # null, a word or a NaN would otherwise blow up mid-comparison. bool is rejected
    # explicitly, being a subclass of int.
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
