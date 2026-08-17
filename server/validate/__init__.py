"""Stage 3 -- decide whether the spin's meters add up.

`current cash = previous cash - bet + win`, in exact `Decimal` arithmetic (`ledger.judge`), compared
against the meter within `ledger.TOLERANCE`. Reads no config at all.
"""

from .runner import read_result, validate_records, validate_run

__all__ = ["validate_run", "validate_records", "read_result"]
