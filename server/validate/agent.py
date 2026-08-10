"""LangChain agent that computes a slot spin's cash value.

The agent reads the three amounts out of the record and calls one tool, `cash_after_spin`,
which does the arithmetic in Decimal. It used to have no tools at all and add the numbers
itself, and that is why it has one now: measured against the local qwen2.5-7b on twelve
records, the model alone got 5/12 right with the original terse prompt and 10/12 when
allowed to show its working. It reliably dropped the `- bet` term -- 1175.76 + 20.00 -
40.00 came back as 1195.76 every single time, deterministically, which is the sample data
this project ships. With the tool it is 12/12, and the tool arguments were parsed
correctly from the record in all twelve.

That distinction matters more than it looks: a Fail is supposed to mean the spin's meters
don't add up. A model that cannot subtract turns every spin into a Fail, and the verdict
stops carrying any information at all. The model still does the part it is good at --
reading three numbers out of a record and deciding what to do with them -- and the answer
taken as authoritative is the tool's return value, not the model's echo of it.

Everything else the original was careful about is kept: temperature 0, no client-side
retries, a strict numeric parser, and a reply that hit the token cap reported rather than
parsed as though it were whole.
"""

import os
import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from urllib.parse import urlsplit

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from .records import FIELDS

DEFAULTS = {
    "base_url": "http://localhost:1234/v1",
    "model": "qwen2.5-7b-instruct-1m",
    "api_key_env": "LMSTUDIO_API_KEY",
    "timeout_s": 120,
}


def _endpoint(url: str) -> str:
    """Append /v1 if it is missing: LM Studio serves the API there, but its
    Developer tab shows a bare host:port, and posting to that answers
    "Unexpected endpoint or method" with no hint of the cause.
    """
    trimmed = url.rstrip("/")

    return trimmed if urlsplit(trimmed).path else f"{trimmed}/v1"


# --- LM Studio -------------------------------------------------------------
# Settings come from config.json's "validate" section; the environment still wins
# over the file, so a different port or model needs no edit. `or` rather than a
# getenv default throughout, so a set-but-empty variable falls back too:
# ChatOpenAI(base_url="") quietly posts the record to api.openai.com.
def endpoint_settings(cfg: dict | None = None) -> tuple[str, str, str, float]:
    """(model, base_url, api_key, timeout) for the configured LLM endpoint."""
    validate_cfg = {**DEFAULTS, **(cfg or {}).get("validate", {})}
    model = os.getenv("LMSTUDIO_MODEL") or validate_cfg["model"]
    base_url = _endpoint(os.getenv("LMSTUDIO_BASE_URL") or validate_cfg["base_url"])
    # LM Studio ignores the key but the client requires a value.
    api_key = os.getenv(validate_cfg["api_key_env"] or "LMSTUDIO_API_KEY") or "lm-studio"
    return model, base_url, api_key, float(validate_cfg["timeout_s"])


# Room for one tool call and a short answer, and no more. 16 was enough when the reply
# was a bare number; a tool call does not fit in it. A reply that hits the cap is still
# reported rather than parsed as though it were complete.
MAX_TOKENS = 200


@tool
def cash_after_spin(cash: str, win: str, bet: str) -> str:
    """Compute the cash meter expected after a spin, as cash + win - bet.

    Pass the three amounts exactly as they appear in the record, as plain decimal
    strings without currency symbols or thousands separators.
    """
    # Decimal, not float: these are currency amounts being compared against another
    # currency amount within half a cent, and 0.1 + 0.2 is famously not 0.3.
    return str(Decimal(cash) + Decimal(win) - Decimal(bet))


TOOL_NAME = cash_after_spin.name

# Written for a 7B, and deliberately short. The formula is named once; the model's job
# is to pull three numbers out of the record and hand them over in the right order,
# which it does reliably. "Never do the arithmetic yourself" is load-bearing -- without
# it the model sometimes answers straight from its head and skips the tool.
SYSTEM_PROMPT = f"""You check a slot machine's cash meter.

You are given three amounts read off the meters before a spin, in this order:

    {",".join(FIELDS)}

The cash meter after the spin should be cash + win - bet.

Call the {TOOL_NAME} tool with those three numbers, passing each one exactly as it
appears in the record. Then reply with only the number the tool returned -- no words,
no currency symbol, no thousands separators.

Never do the arithmetic yourself. The tool's answer is the only correct one."""

# --- Reply parsing ---------------------------------------------------------
# An optional sign, digits, one optional decimal point. Deliberately strict,
# because Decimal() also accepts "nan", "inf", "1e3" and "1_155.76".
_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?\Z")

# "1,155.76" is a thousands separator. "1155,76" is a decimal comma and means
# a hundred times less, so only the unambiguous grouped form is stripped.
_GROUPED = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?\Z")

# Currency symbols and Unicode dashes a model may decorate an amount with.
# Whitespace is only stripped from the ends: removing it from the middle would
# glue "11 55.76" into a number the model never said.
_CURRENCY = re.compile(r"[$€£¥]")
_MINUS = str.maketrans({"−": "-", "–": "-", "—": "-"})


@lru_cache(maxsize=4)
def build_agent(model: str, base_url: str, api_key: str, timeout: float):
    """The agent and its one tool, built once per endpoint and reused.

    Keyed on the settings rather than cached on module constants, so the server
    picking up an edited config.json builds a new client instead of quietly going on
    talking to the old endpoint.
    """
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

    return create_agent(llm, [cash_after_spin], system_prompt=SYSTEM_PROMPT)


def format_record(values: dict[str, Decimal]) -> str:
    """Render the before-spin values as the record the prompt describes.

    Values go out exact, padded to two decimals. Rounding here would be
    charged to the model, which can only answer from the digits it is handed:
    precision dropped now returns as a mismatch against the after-spin cash,
    and three roundings outrun the tolerance meant to absorb them.
    """
    return ",".join(_pad(values[field]) for field in FIELDS)


def _pad(value: Decimal) -> str:
    """The exact value, padded to at least two decimal places."""
    # :f rather than str(), which would render a large value as 1.2E+3.
    whole, _, fraction = f"{value:f}".partition(".")

    return f"{whole}.{fraction.ljust(2, '0')}"


def compute_cash(record: str, cfg: dict | None = None) -> Decimal:
    """Send the before-spin record to the agent, return the computed Cₙ.

    The answer is the tool's return value, read back out of the message history --
    not the model's closing sentence. The model does agree with the tool in practice
    (12/12 on the measured records), but where the two can differ the arithmetic is
    the tool's and only the tool's, and a disagreement is worth raising rather than
    silently resolving.
    """
    question = f"{record}\n\nCompute the cash value."

    state = build_agent(*endpoint_settings(cfg)).invoke({"messages": [("user", question)]})
    message = state["messages"][-1]

    if message.response_metadata.get("finish_reason") == "length":
        raise ValueError(
            f"model reply hit the {MAX_TOKENS} token cap and may be cut off, "
            f"it said: {str(message.text)!r}"
        )

    computed = _tool_result(state)
    if computed is None:
        raise ValueError(
            f"the model answered without calling {TOOL_NAME}, so nothing computed the "
            f"arithmetic. It said: {str(message.text)!r}"
        )

    # .text concatenates plain string content and content blocks alike, so a
    # number split across blocks survives.
    reply = str(message.text).strip()
    try:
        echoed = to_decimal(reply) if reply else None
    except ValueError:
        # Closing prose rather than a bare number ("The cash value is 1155.76.").
        # The tool has already answered, so this is not worth failing over.
        echoed = None

    if echoed is not None and echoed != computed:
        raise ValueError(
            f"the model reported {reply!r} but {TOOL_NAME} computed {computed} from "
            f"record {record} -- refusing to guess which is meant"
        )

    return computed


def _tool_result(state) -> Decimal | None:
    """The last value cash_after_spin returned, or None if it was never called."""
    for message in reversed(state["messages"]):
        if getattr(message, "type", None) == "tool" and message.name == TOOL_NAME:
            try:
                return Decimal(str(message.content).strip())
            except InvalidOperation as exc:
                raise ValueError(
                    f"{TOOL_NAME} returned something that is not a number: "
                    f"{message.content!r}"
                ) from exc
    return None


def to_decimal(reply: str) -> Decimal:
    """Parse the model's numeric reply, rejecting anything ambiguous."""
    cleaned = _CURRENCY.sub("", reply.translate(_MINUS)).strip()

    if _GROUPED.match(cleaned):
        cleaned = cleaned.replace(",", "")

    if not _NUMBER.match(cleaned):
        raise ValueError(f"model did not answer with a plain number, it said: {reply!r}")

    return Decimal(cleaned)
