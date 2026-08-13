"""The LLM that judges one spin. One call, no tools, a fixed JSON reply.

Both sets of meters go to a local model (LM Studio by default) together with the formula,
and it answers with a `Verdict` object. **The model owns the verdict outright** -- nothing
in Python adds these numbers up or compares them. That is deliberate: the point of this
stage is a model checking the cabinet's arithmetic, and a Python cross-check would only be
answering the question a second time.

That is only survivable on a 7B because of the shape of `Verdict`, and every part of that
shape was measured over the eight records in the table below. The predecessor of this
module asked the same model for one word, yes or no, and scored **0/6** -- not unreliable
but inverted, deterministically, at temperature 0.

Three findings, all against the local qwen2.5-7b through LM Studio:

- **`working` must come before the numbers.** The model fills the fields in schema order,
  so a schema that asks for `computed_cash` first is asking it to produce the answer cold,
  which is the same one-forced-token trap as the yes/no design. Deleting this one field and
  changing nothing else takes the shipped schema from **8/8 to 2/8** on the verdicts and
  8/8 to 0/8 on the sums: it answered `2909.60 - 1.00 = 2908.60`, dropping the win outright.
  Reordering these fields is not cosmetic; it is that measurement.

  That control run is also why `records.PREVIOUS_FIELDS` no longer carries `win`. The
  prompt at the time sent `pre_spin`'s stale WIN meter with a line saying to ignore it, and
  on one record the model used it anyway. Telling a model not to look at a number is weaker
  than not handing it the number; nothing sent up now is anything but a term of the sum.
- **The amounts are `float`, not `str`, and that is the only thing keeping them numbers.**
  A `str` field constrains nothing, and the model fills it with a placeholder rather than
  arithmetic: over those same eight records it returned `"logarithmic"`, `"in_range"`,
  `"synced"`, `"TBD"` and `"in this case: 2926.70"` -- **0/8** usable, while the verdict
  beside them was right. Typed as numbers the grammar cannot emit anything but digits, and
  the sums went to **8/8**. This is the one place in the codebase money is a float, and it
  is safe only because nothing compares against it: the exact `Decimal`s are what go *to*
  the model, and what comes back is its own working, formatted for the ledger.
- **A JSON Schema `pattern` cannot be used to do that instead.** `^-?\\d+\\.\\d{2}$` on a
  string field makes LM Studio answer **400** on every request -- its grammar engine fails
  to initialise from the regex. Constraint here has to come from the field's *type*.

`git log` has the design that scored 12/12: a `cash_after_spin` tool doing the sum in
`Decimal`. It is where to go back to if these verdicts stop being trustworthy -- and
re-measure on any model change, because none of these numbers transfer.

`endpoint_settings()` reads config.json's "validate" section with the LMSTUDIO_*
environment variables winning over the file, and `server/api.py`'s /api/health imports it
to check LM Studio is serving the configured model. `build_model` is keyed on those
settings rather than on module constants, so a server picking up an edited config.json
builds a new client instead of quietly going on talking to the old endpoint.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from .records import CURRENT_FIELDS, PREVIOUS_FIELDS

DEFAULTS = {
    "base_url": "http://localhost:1234/v1",
    "model": "qwen2.5-7b-instruct-1m",
    "api_key_env": "LMSTUDIO_API_KEY",
    "timeout_s": 120,
}

# What the model is asked to check, and what the UI prints under the ledger heading.
FORMULA = "current cash = previous cash - bet + win"

# Room for the JSON object. The old one-word design capped this at 8 tokens; a structured
# reply that hits the cap is truncated JSON, which `judge` reports as such rather than
# letting it surface as a parse error nobody can act on.
MAX_TOKENS = 256


class Verdict(BaseModel):
    """The only shape a reply may take. **Field order is the reasoning order.**

    The model writes these in the order they are declared, so it works the sum out in
    prose, states the result, subtracts, and only then judges. Every one of the four is
    load-bearing and the ordering was measured -- see the module docstring.
    """

    working: str = Field(
        description="the subtraction and addition written out in full, "
                    "e.g. '2909.60 - 1.00 + 18.10 = 2926.70'")
    computed_cash: float = Field(description="the result of the working")
    difference: float = Field(
        description="computed_cash minus the current cash meter")
    verdict: Literal["pass", "fail"] = Field(
        description="pass if the difference is within the allowed tolerance, else fail")


# Written for a 7B: the formula is spelled out as a numbered procedure rather than stated
# as algebra, which measurably improves reliability. Every value named here is one the model
# needs -- `pre_spin`'s stale WIN meter is not sent at all, so there is nothing to instruct
# it to ignore and nothing for it to reach for. See `records.PREVIOUS_FIELDS`.
SYSTEM_PROMPT_TEMPLATE = """You audit one slot machine spin against a single formula:

    current cash = ((previous cash + win) - bet)

You are given two sets of meter readings from the same spin:

    previous   the meters before the spin: cash, bet
    current    the meters after it settled: cash, win

Work in this order:
1. computed_cash = ((previous.cash + current.win) - previous.bet)
2. difference = computed_cash - current.cash
3. If difference is between -{tolerance} and {tolerance}, the verdict is "pass".
   Otherwise the verdict is "fail".

Amounts are plain decimal numbers to two places, with no currency symbol and no
thousands separator."""


def _endpoint(url: str) -> str:
    """Append /v1 if it is missing: LM Studio serves the API there, but its
    Developer tab shows a bare host:port, and posting to that answers
    "Unexpected endpoint or method" with no hint of the cause.
    """
    trimmed = url.rstrip("/")

    return trimmed if urlsplit(trimmed).path else f"{trimmed}/v1"


# Settings come from config.json's "validate" section; the environment still wins over the
# file, so a different port or model needs no edit. `or` rather than a getenv default
# throughout, so a set-but-empty variable falls back too: ChatOpenAI(base_url="") quietly
# posts the record to api.openai.com.
def endpoint_settings(cfg: dict | None = None) -> tuple[str, str, str, float]:
    """(model, base_url, api_key, timeout) for the configured LLM endpoint."""
    validate_cfg = {**DEFAULTS, **(cfg or {}).get("validate", {})}
    model = os.getenv("LMSTUDIO_MODEL") or validate_cfg["model"]
    base_url = _endpoint(os.getenv("LMSTUDIO_BASE_URL") or validate_cfg["base_url"])
    # LM Studio ignores the key but the client requires a value.
    api_key = os.getenv(validate_cfg["api_key_env"] or "LMSTUDIO_API_KEY") or "lm-studio"
    return model, base_url, api_key, float(validate_cfg["timeout_s"])


@lru_cache(maxsize=4)
def build_model(model: str, base_url: str, api_key: str, timeout: float):
    """The chat model bound to the `Verdict` schema, built once per endpoint and reused."""
    llm = ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=0,
        max_tokens=MAX_TOKENS,
        # The openai SDK retries twice by default, which would turn a wedged
        # server into three timeouts -- six silent minutes -- before we print.
        max_retries=0,
        timeout=timeout,
    )

    # json_schema is LM Studio's constrained decoding, and it is also the one method here
    # that is not a tool call in disguise -- `function_calling` would send this as a tool.
    # include_raw keeps the unparsed message, which is the only way to tell a model that
    # answered badly from one that was cut off at MAX_TOKENS.
    return llm.with_structured_output(Verdict, method="json_schema", include_raw=True)


def format_records(previous: dict[str, Decimal], current: dict[str, Decimal]) -> str:
    """The two sets of meters as the JSON object the model is asked about.

    Values go out exact, padded to two decimals. Rounding here would be charged to the
    model, which can only answer from the digits it is handed.
    """
    return json.dumps(
        {"previous": {f: pad(previous[f]) for f in PREVIOUS_FIELDS},
         "current": {f: pad(current[f]) for f in CURRENT_FIELDS}},
        indent=2)


def pad(value: Decimal) -> str:
    """The exact value, padded to at least two decimal places."""
    # :f rather than str(), which would render a large value as 1.2E+3.
    whole, _, fraction = f"{value:f}".partition(".")

    return f"{whole}.{fraction.ljust(2, '0')}"


def judge(previous: dict[str, Decimal], current: dict[str, Decimal],
          tolerance: Decimal, cfg: dict | None = None) -> Verdict:
    """Send both sets of meters to the model and return its verdict object."""
    messages = [
        ("system", SYSTEM_PROMPT_TEMPLATE.format(tolerance=pad(tolerance))),
        ("user", f"{format_records(previous, current)}\n\n"
                 "Do these two readings satisfy the formula?"),
    ]

    reply = build_model(*endpoint_settings(cfg)).invoke(messages)
    if reply["parsed"] is None:
        raise ValueError(f"the model did not answer in the required format -- "
                         f"{_why_unparsed(reply)}")

    return reply["parsed"]


def _why_unparsed(reply: dict) -> str:
    """Say why a reply could not be read, rather than surfacing a schema traceback."""
    raw = reply["raw"]
    if raw.response_metadata.get("finish_reason") == "length":
        return (f"it was cut off at the {MAX_TOKENS} token cap. Raise MAX_TOKENS in "
                "server/validate/agent.py, or use a model that answers more briefly")

    if raw.additional_kwargs.get("reasoning_content"):
        return (f"it spent the {MAX_TOKENS} token cap reasoning. Use a non-reasoning "
                "model, or set validate.model in config.json to one")

    return (f"{reply['parsing_error']}. If the server rejected the schema outright, it "
            "does not support response_format: json_schema -- check validate.base_url "
            "in config.json points at LM Studio")
