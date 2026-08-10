"""LangChain agent that validates a slot spin. No tools -- the model itself
computes the cash value and answers yes or no.

The model owns the whole judgement: it adds, it compares, and its one word becomes the
verdict. `runner.py` also computes `cash + win - bet` in `Decimal`, but only to fill the
ledger the UI draws -- that number never overrides the model. When the two disagree the
message says so, which is the only warning you get that the answer was reached wrongly.

Know the cost before trusting a verdict from this. Measured against the local qwen2.5-7b
over twelve records, the model doing the arithmetic alone scored 5/12 and dropped the
`- bet` term deterministically; folding the comparison in as well puts the arithmetic
*and* the judgement in a single forced token, which is where a small model is least
reliable. If the verdicts stop being trustworthy, `git log` has two better-measured
designs: the model returning a bare number with Python comparing (the same 5/12
arithmetic, but a comparison that cannot be wrong), and a `cash_after_spin` tool doing
the sum in `Decimal` (12/12).

Three things differ from the standalone script this came from, and all three are what let
it run inside the server rather than from a shell:

- `endpoint_settings()` reads config.json's "validate" section, with the LMSTUDIO_*
  environment variables still winning over the file. `server/api.py`'s /api/health imports
  it to check LM Studio is serving the configured model.
- `build_agent` is keyed on those settings rather than on module constants, so a server
  picking up an edited config.json builds a new client instead of quietly going on talking
  to the old endpoint.
- `FIELDS` comes from `records.py`, which is also where the extract step's keys are named,
  so there is one definition of the record's shape.

Everything the original was careful about is kept: temperature 0, no client-side retries,
an empty reply reported with the reason rather than as '', and a reply that is neither
yes nor no raised rather than guessed at.
"""

import os
from decimal import Decimal
from functools import lru_cache
from urllib.parse import urlsplit

from langchain.agents import create_agent
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


# One word is the whole answer. Small enough that a model minded to explain itself gets
# cut off rather than talked round, and `to_verdict` reads only the first word anyway.
MAX_TOKENS = 8

# Written for a 7B: the formula is spelled out as an arithmetic procedure rather than
# stated as algebra, which measurably improves reliability. Plain ASCII subscripts, and
# the bracket around the addition, are both there to stop the model dropping the `- bet`
# term -- the one error it makes deterministically.
#
# Record 2 is its cash value alone, not the cash,win,bet triple record 1 uses: step 3
# only needs cash2, and the after-spin frame's WIN and BET meters genuinely read blank,
# so there is nothing honest to put in them.
SYSTEM_PROMPT = f"""You validate slot machine records using this formula:

Cₙ = (Cₙ₋₁ + Wₙ₋₁) − Bₙ₋₁

C = cash amount, W = win amount, B = bet amount, n = iteration number.

Record 1 is given as: {",".join(FIELDS)}
Record 2 is given as its cash value alone.

You will be given record 1 and record 2. Do this:
1. Take cash, win and bet from record 1.
2. Compute: cash1 + win1 - bet1
3. Compare the result with cash2 (the cash value of record 2).
4. If they are equal, the answer is yes. If not, the answer is no.

Your answer must be exactly one word: yes or no.
Do not show your working. Do not add punctuation or any other text."""


@lru_cache(maxsize=4)
def build_agent(model: str, base_url: str, api_key: str, timeout: float):
    """A LangChain agent with an empty tool list, built once per endpoint and reused.

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

    return create_agent(llm, [], system_prompt=SYSTEM_PROMPT)


def format_record(values: dict[str, Decimal]) -> str:
    """Render the before-spin values as record 1: the cash,win,bet triple.

    Values go out exact, padded to two decimals. Rounding here would be
    charged to the model, which can only answer from the digits it is handed:
    precision dropped now returns as a mismatch against the after-spin cash,
    and three roundings outrun the tolerance meant to absorb them.
    """
    return ",".join(_pad(values[field]) for field in FIELDS)


def format_cash(value: Decimal) -> str:
    """Render the after-spin cash as record 2, on the same terms as record 1."""
    return _pad(value)


def _pad(value: Decimal) -> str:
    """The exact value, padded to at least two decimal places."""
    # :f rather than str(), which would render a large value as 1.2E+3.
    whole, _, fraction = f"{value:f}".partition(".")

    return f"{whole}.{fraction.ljust(2, '0')}"


def ask(record_1: str, record_2: str, cfg: dict | None = None) -> str:
    """Send both records to the model and return its raw reply."""
    question = (
        f"1. {record_1}\n"
        f"2. {record_2}\n\n"
        "Do records 1 and 2 satisfy the validation formula?"
    )

    print("question",question)

    state = build_agent(*endpoint_settings(cfg)).invoke({"messages": [("user", question)]})
    message = state["messages"][-1]

    # .text concatenates plain string content and content blocks alike, so a
    # reply split across blocks survives.
    reply = str(message.text)

    print("reply",reply)

    if not reply.strip():
        raise ValueError(f"model returned {_why_empty(message)}")

    return reply.strip()


def _why_empty(message) -> str:
    """Say why a reply carried no text, rather than reporting ''."""
    if getattr(message, "tool_calls", None):
        return "a tool call, but this agent has no tools"

    if message.additional_kwargs.get("reasoning_content"):
        return (
            "reasoning but no answer -- a reasoning model needs more than the "
            f"{MAX_TOKENS} token cap"
        )

    return "an empty reply"


def to_verdict(reply: str) -> str:
    """Map the model's yes/no onto the verdict vocabulary."""
    # The first word, compared whole. A `startswith("no")` prefix test -- which is what
    # this used to be -- reads "not sure" and "none of them" as a confident Fail, and a
    # wrong verdict that looks certain is the one failure this stage must not produce.
    words = reply.strip().lower().split()
    answer = words[0].strip(".,;:!?\"'") if words else ""

    print("answer=======================>",answer)

    if answer == "yes":
        return "pass"
    if answer == "no":
        return "fail"

    raise ValueError(f"model did not answer yes or no, it said: {reply!r}")
