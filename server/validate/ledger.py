"""The arithmetic that judges one spin. Pure Python, exact `Decimal`.

    computed_cash = (previous.cash + current.win) - previous.bet
    difference    = computed_cash - current.cash
    verdict       = pass if -tolerance <= difference <= tolerance else fail

Two things the rest of the stage is written against. **`Verdict`'s field order** is read by
`runner`, `validate.json`, the CLI and the UI ledger. And **money stays `Decimal` end to end** --
the comparison *is* the verdict, so nothing here may become a float. The tolerance lives beside the
comparison it governs rather than in config, but `judge` takes it as an argument.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, NamedTuple

# What is checked, and what the UI prints under the ledger heading.
FORMULA = "current cash = previous cash - bet + win"

# Half a cent, i.e. rounding noise rather than a real mismatch. A Decimal, not a float, so the
# boundary sits exactly there instead of wherever binary float lands.
TOLERANCE = Decimal("0.005")


class Verdict(NamedTuple):
    """One spin judged: the working, the two numbers behind it, and the answer."""

    working: str
    computed_cash: Decimal
    difference: Decimal
    verdict: Literal["pass", "fail"]


def pad(value: Decimal) -> str:
    """The exact value, padded to at least two decimal places."""
    # :f rather than str(), which would render a large value as 1.2E+3.
    whole, _, fraction = f"{value:f}".partition(".")

    return f"{whole}.{fraction.ljust(2, '0')}"


def judge(previous: dict[str, Decimal], current: dict[str, Decimal],
          tolerance: Decimal = TOLERANCE) -> Verdict:
    """Work the spin's ledger out and say whether the cash meter follows from it.

    `previous` supplies cash and bet, `current` cash and win. Which frame each comes from is
    `runner.find_records`, and that is the correctness question in this stage, not the arithmetic.
    """
    computed_cash = (previous["cash"] - previous["bet"]) + current["win"]
    difference = computed_cash - current["cash"]

    working = (f"{pad(previous['cash'])} - {pad(previous['bet'])} + {pad(current['win'])} "
               f"= {pad(computed_cash)}, and the meter read {pad(current['cash'])} "
               f"-- a difference of {pad(difference)}")

    # <= so a difference sitting exactly on the tolerance passes. Unreachable at half a cent, but a
    # caller may pass a wider one.
    verdict = "pass" if abs(difference) <= tolerance else "fail"

    return Verdict(working, computed_cash, difference, verdict)
