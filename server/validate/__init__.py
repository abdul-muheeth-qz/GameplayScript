"""Step 3 -- decide whether the spin's meters add up.

The check is `current cash = previous cash - bet + win`: the cash meter after a spin should
be the cash before it, less the bet that was placed, plus the win this spin paid. A local
LLM (LM Studio by default, configured under "validate" in config.json) is handed both sets
of meters and answers with a structured verdict. Nothing in Python does the arithmetic.

    python -m server.validate.cli captured_files/<run_id>
    python -m server.validate.cli captured_files/<run_id> --json
"""

from .runner import read_result, validate_records, validate_run

__all__ = ["validate_run", "validate_records", "read_result"]
