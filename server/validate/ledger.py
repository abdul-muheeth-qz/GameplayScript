"""The arithmetic that judges one spin. Pure Python, exact `Decimal`, no model.

This replaced an LLM. Both sets of meters used to go to a local model (LM Studio) with
the formula written out as a numbered procedure, and it answered with a structured
verdict; the model owned every number and nothing here added them up. The sum it was
being asked for is three terms long and the inputs are already exact `Decimal`s by the
time they reach this module, so it is now done here:

    computed_cash = (previous.cash + current.win) - previous.bet
    difference    = computed_cash - current.cash
    verdict       = pass if -tolerance <= difference <= tolerance else fail

`git log` still holds the model version -- its schema, and the eight-record table that
measured every part of that schema's shape -- if a model is ever wanted back.

Two things carried over from it deliberately, because the rest of the stage is written
against them:

- **The reply shape is unchanged.** `Verdict` still has `working`, `computed_cash`,
  `difference` and `verdict` in that order, so `runner.validate_records`, `validate.json`
  and the UI's ledger did not have to move. `working` is no longer a model talking itself
  through the sum -- it is the sum, written out, which is what the CLI prints and what the
  UI falls back to when the meters could not be read as numbers.
- **Money stays `Decimal` end to end.** The model version was the one place in the codebase
  money became a float, and it was safe only because nothing compared against those floats.
  Here the comparison *is* the verdict, so the terms, the sum, the difference and the
  tolerance are all `Decimal` and the boundary sits exactly on half a cent instead of
  wherever binary float lands.

The tolerance is an argument rather than a constant here: `runner.DEFAULT_TOLERANCE` is the
default and `config.json`'s `validate.tolerance` overrides it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, NamedTuple

# What is checked, and what the UI prints under the ledger heading.
FORMULA = "current cash = previous cash - bet + win"


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
          tolerance: Decimal) -> Verdict:
    """Work the spin's ledger out and say whether the cash meter follows from it.

    `previous` supplies cash and bet, `current` supplies cash and win -- which frame each
    of those is read from is `runner.find_records`, and it is the correctness question in
    this stage, not the arithmetic.
    """
    computed_cash = (previous["cash"] - previous["bet"]) + current["win"]
    difference = computed_cash - current["cash"]

    working = (f"{pad(previous['cash'])} - {pad(previous['bet'])} + {pad(current['win'])} "
               f"= {pad(computed_cash)}, and the meter read {pad(current['cash'])} "
               f"-- a difference of {pad(difference)}")

    # abs() rather than the two comparisons, and <= so a difference sitting exactly on the
    # tolerance passes. With money at two places and a tolerance of half a cent that
    # boundary is unreachable, but the tolerance is configurable and may not stay so.
    verdict = "pass" if abs(difference) <= tolerance else "fail"

    return Verdict(working, computed_cash, difference, verdict)
