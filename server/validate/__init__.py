"""Step 3 -- decide whether the spin's meters add up.

The check is `current cash = previous cash - bet + win`: the cash meter after a spin should
be the cash before it, less the bet that was placed, plus the win this spin paid. The sum is
worked out in exact `Decimal` arithmetic (`ledger.judge`) and compared against the meter
within `validate.tolerance`.

    python -m server.validate.cli server/captured_files/<run_id>
    python -m server.validate.cli server/captured_files/<run_id> --json
"""

from .runner import read_result, validate_records, validate_run

__all__ = ["validate_run", "validate_records", "read_result"]
