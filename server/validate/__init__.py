"""Step 3 -- decide whether the spin's meters add up.

The check is Cₙ = Cₙ₋₁ + Wₙ₋₁ - Bₙ₋₁: the cash meter after a spin should be the cash
before it, plus the win that was standing, less the bet that was placed. A local LLM
(LM Studio by default, configured under "validate" in config.json) does the arithmetic;
Python compares its answer to the after-spin meter within half a cent.

    python -m server.validate.cli captured_files/<run_id>
    python -m server.validate.cli server/validate/data --json
"""

from .runner import read_result, validate_records, validate_run

__all__ = ["validate_run", "validate_records", "read_result"]
