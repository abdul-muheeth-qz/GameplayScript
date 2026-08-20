"""Which payline set and which paytable a denomination selects, and where the denomination comes from.

The same reels pay different lines at different denominations. On this game the five lines the
audit has always walked -- middle, top, bottom, V, inverted V -- are only the lines every denom
shares: 5c and 10c pay 20, and 1c and 2c pay 40, the extra ones being a superset with those five
first. Each denomination also has its **own reel layout**, so the reel-stop checkpoint must map a
spin's stops through that denomination's paytable and not another's.

Both of those are per-denomination facts about one game, so both live in that game's block in
game_config.json, beside its `payline_geometry` and its `reel_strips`:

    games["FortuneOx.exe"] = {
      "payline_sets": {"40_line": {"label": ..., "lines": [{"id", "name", "cells"}, ...]}, ...},
      "denoms":       {"1c": {"paylines": "40_line", "reel_strips": {"path", "placeholders"}}, ...}
    }

**`payline_sets` is named rather than inlined per denomination because the sets are shared** -- 1c
and 2c pay the identical 40 lines and 5c and 10c the identical 20, so inlining would put four copies
of two line lists in one file, and two copies of a table is how one of them ends up stale and
believed. The paytables are *not* shared and so are named per denomination: 1c and 2c happen to hold
the same strips today and 5c and 10c likewise, but that is a fact about this build's data, not about
the game, and a denomination pointing at its own sheet cannot be wrong about it later.

The denomination itself
-----------------------

**`server/denom.json`, at that one fixed path, and its `denom.text` is the answer.** It is written
by whatever reads the credit and denomination off the screen; this module only reads it. The
structure is fixed and only `denom.text` decides anything:

    {"play_credit": {"text": "880 credits", "value": 880, "unit": "credits",
                     "available": [88, 176, 264, 440, 880]},
     "denom":       {"text": "1c", "value": 1, "unit": "cents",
                     "available": ["1c", "2c", "5c", "10c", "$1.00", "$2.00"]}}

There is no config key and no CLI flag for the path: one fixed location, `settings.SERVER_DIR`'s
`denom.json`, beside the two config files. A second place to point at is a second place for the
answer to come from.

`normalize` is what lets that file say `"$1.00"` while the config key is `"$1.00"` and the asset is
named `paytable_excel_1$.xlsx` -- one denomination, several spellings, canonicalised once here. It
refuses to canonicalise a bare number: `"1"` is `$1` or `1c` depending on who wrote it, and this is
the one place in the audit where guessing wrong changes which lines are walked.

**The game's own log is read too, and it never decides.** `gamelog` parses
`[WagerGameApp.UpdateDenom] New denom[1.000]` into a `denom_changed` event, `spin.py` writes it into
`spin.json`, and `classify` lifts it to that file's top-level `denom` -- so every run folder already
carries, in cents, the denomination the *game* thought it was running. `select` prices the file's
answer and compares. They are reported together in `payline.json` (`logged_cents`, `agrees_with_log`)
and a disagreement is **logged as a warning and put on the record**, never resolved silently.

That is worth the few lines because the file cannot follow the spin and the log can. `denom.json`
holds one value for the whole cabinet; run `2026-08-19_145308` on this disk is a real $2.00 spin
sitting between two 1c spins, and a file left saying `1c` would walk 39 lines over it. The file
stays the source, because that is what it is for -- but nothing here will let that happen *quietly*.

Reading the log value has three rules of its own:

  * **The game logs cents**, not a spelling: `1.000`, `100.000` and `200.000` are all that appear
    across every run folder here. So the comparison is arithmetic -- `cents_of` prices the file's
    text and compares numbers -- rather than a table of strings in this module.
  * **The last `denom_changed` wins, `belongs_to` included.** `classify` excludes events tagged
    `belongs_to` because they are the previous, uncollected win being resolved by our press -- but a
    denomination is *state*, not an outcome. It is logged on every activation as well as on a real
    change, so on a carried win it lands inside the old spin's block: two run folders here are
    exactly that, and excluding them would leave the carried-win spins with no log value to compare
    against at all.
  * **Older run folders carry it only in `events`.** `classify` writes the top-level field from now
    on; `logged_denom` reads both, which is one value in two places within one file rather than a
    fallback to another source.

Finally, **a game with no `denoms` block reads the base set, and that is the block's job to say.**
The block's presence is this game's declaration that denominations change what pays; without one
there is nothing per-denomination to choose and `payline_geometry.paylines` is the answer. But a game
that *has* the block and no entry for the denomination in the file **raises**, listing the ones it
has -- reaching the base set that way would report five lines for a spin that pays forty, and nothing
downstream would catch it.
"""

from __future__ import annotations

import json
import logging
import os
import re
from decimal import Decimal, InvalidOperation

from ..settings import SERVER_DIR
from .geometry import PaylineError

LOG = logging.getLogger("payline")

# The keys a game's block carries these under, in game_config.json.
DENOMS_KEY = "denoms"
SETS_KEY = "payline_sets"

# **The one fixed path.** Beside config.json and game_config.json, in `server/`, because that is
# where this package's inputs live and `settings.SERVER_DIR` is the anchor everything else uses.
# Deliberately not a config key: see the module docstring.
DENOM_FILE_NAME = "denom.json"
DENOM_FILE = os.path.join(SERVER_DIR, DENOM_FILE_NAME)

# What the capture stage leaves in a run folder, and the event inside it that carries the game's own
# denomination. Both are `capture`'s names; this module reads them and never writes them.
SPIN_FILE = "spin.json"
DENOM_EVENT = "denom_changed"

# The retired config key. Warned about rather than ignored -- a setting that silently stopped being
# read is the same trap `payline.reel_stops.strips` was.
RETIRED_SETTING = "denom_file"

# "1c", "10c", "12.5c" -- a number with an explicit cents mark. A bare number is deliberately not
# matched: see `normalize`.
_CENTS_RE = re.compile(r"(\d+(?:\.\d+)?)(?:c|¢|cent|cents)")


def normalize(text) -> str:
    """One denomination's canonical spelling. `"$1.00"`, `"1$"` and `"$1"` are all `"$1"`.

    Applied to `denom.text` and to every config key before either is looked up, so neither has to be
    written the other's way. Anything it does not recognise comes back stripped and lowered rather
    than transformed, which makes it fail by name instead of matching the wrong block.

    A **bare number is not canonicalised**: `"1"` is $1 or 1c depending on who wrote it, and this is
    the one place where guessing wrong changes which lines are walked.
    """
    s = str(text or "").strip().lower().replace(" ", "").replace(",", "")
    if not s:
        return ""

    # A trailing currency mark is the same statement as a leading one: the asset for "$1.00" is
    # named paytable_excel_1$.xlsx.
    if s.endswith("$"):
        s = "$" + s[:-1]

    if s.startswith("$"):
        body = s[1:]
        # "$1.00" and "$1" are one denomination -- zeros after the point carry no information.
        if "." in body:
            body = body.rstrip("0").rstrip(".")
        return f"${body}" if body else s

    match = _CENTS_RE.fullmatch(s)
    if match:
        number = match.group(1)
        if "." in number:
            number = number.rstrip("0").rstrip(".")
        return f"{number}c"

    return s


def cents_of(canonical: str) -> Decimal | None:
    """`"1c"` -> 1, `"$1"` -> 100. None for a spelling that cannot be priced.

    This is what compares the file's denomination to the game's logged number, and what lets a game
    with denominations other than this one's six need a config edit and no code edit. `Decimal`,
    because these are money and `0.1 + 0.2` is not 0.3.
    """
    text = (canonical or "").strip()
    try:
        if text.startswith("$"):
            return Decimal(text[1:]) * 100
        if text.endswith("c"):
            return Decimal(text[:-1])
    except InvalidOperation:
        return None
    return None


class DenomSelection:
    """What one denomination decided: which lines to walk, and whose paytable to map the stops through.

    Also the "no denomination to apply" answer, where both are None and the game's own block supplies
    them -- so every caller threads one object rather than branching on whether one was found.
    """

    def __init__(self, *, text=None, raw=None, value=None, unit=None, available=None,
                 source="", logged=None, disagreement=None, set_name=None, set_label=None,
                 note=None, lines=None, strips_block=None, strips_where=None):
        self.text = text
        # Exactly what denom.json said, before normalizing -- so "which line of that file produced
        # this?" stays answerable from the record alone.
        self.raw = raw
        self.value = value
        self.unit = unit
        self.available = list(available or [])
        self.source = source
        # The game's own number, in cents, for comparison only. Never decides anything.
        self.logged = logged
        self.disagreement = disagreement
        self.set_name = set_name
        self.set_label = set_label
        self.note = note
        # None means "the game's own", for both of these. Neither is ever another denomination's.
        self.lines = lines
        self.strips_block = strips_block
        self.strips_where = strips_where

    @property
    def configured(self) -> bool:
        return self.text is not None

    def describe(self) -> dict:
        """The `denom` block of payline.json -- which denomination, read from where, and what it chose.

        `note` is here rather than only in the config because a set that is knowingly incomplete has
        to say so on the record that was reached with it, and `disagreement` because a file that has
        drifted from the cabinet is exactly the thing a reader has to be able to see after the fact.
        """
        return {
            "denom": self.text,
            "text": self.raw,
            "value": self.value,
            "unit": self.unit,
            "available": self.available,
            "source": self.source,
            # What the game's own log said this spin was, in cents. Null when the run folder has no
            # captured spin, or its log never recorded one.
            "logged_cents": str(self.logged) if self.logged is not None else None,
            "agrees_with_log": (None if self.logged is None or not self.configured
                                else self.disagreement is None),
            "disagreement": self.disagreement,
            "payline_set": self.set_name,
            "payline_set_label": self.set_label,
            "lines": len(self.lines) if self.lines is not None else None,
            "note": self.note,
            "paytable": (self.strips_block or {}).get("path"),
        }


def base_selection(reason: str, logged=None) -> DenomSelection:
    """No denomination to apply: the game's own payline set and paytable, and why."""
    return DenomSelection(source=reason, logged=logged)


# -- reading denom.json ----------------------------------------------------

def read_denom_file(path: str | None = None) -> dict | None:
    """`denom.json`'s `denom` block, or None when the file is not there.

    Only `text` decides anything; `value`, `unit` and `available` are carried into the record because
    they are the reader's own account of the same thing and a disagreement between `"1c"` and
    `value: 5` is worth being able to see after the fact.

    `DENOM_FILE` is read **inside** rather than as a default argument, which binds at import: the
    test module swaps the constant to avoid touching the cabinet's real file, and a default would
    have made every one of those tests silently read the shipped file instead.
    """
    path = path or DENOM_FILE
    if not os.path.isfile(path):
        return None

    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise PaylineError(
            f"{path} could not be read as JSON ({exc}). It must hold "
            f"{{\"denom\": {{\"text\": \"1c\", \"value\": 1, \"unit\": \"cents\", "
            f"\"available\": [...]}}, \"play_credit\": {{...}}}}") from None

    block = (data or {}).get("denom") if isinstance(data, dict) else None
    if not isinstance(block, dict) or not str(block.get("text") or "").strip():
        raise PaylineError(
            f"{path} has no \"denom\": {{\"text\": ...}}, so there is nothing in it to say which "
            f"denomination was played and therefore which payline set to walk. It must hold "
            f"{{\"denom\": {{\"text\": \"1c\", \"value\": 1, \"unit\": \"cents\", "
            f"\"available\": [...]}}, \"play_credit\": {{...}}}}; got "
            f"{json.dumps(data)[:200]}")
    return block


# -- reading the game's own value, for comparison only ---------------------

def read_spin(run_dir: str | None) -> dict | None:
    """The run folder's `spin.json`, or None when it has none (a loose image, a folder mid-capture).

    Unreadable is None too, not an error: this value is a cross-check and must not be able to fail an
    audit that `denom.json` has already answered.
    """
    if not run_dir:
        return None
    path = os.path.join(run_dir, SPIN_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8-sig") as fh:
            record = json.load(fh)
    except (OSError, ValueError) as exc:
        LOG.warning("%s could not be read (%s), so the game's own denomination cannot be "
                    "compared against %s", path, exc, DENOM_FILE_NAME)
        return None
    return record if isinstance(record, dict) else None


def logged_denom(spin: dict | None) -> Decimal | None:
    """The denomination the game logged for this spin, in cents.

    `classify` lifts it to `spin.json`'s top-level `denom`; older folders have it only in the event
    list, so that is read too -- one value in two places within one file, not a second source. The
    **last** event wins and `belongs_to` is not excluded: see the module docstring.
    """
    if not spin:
        return None

    def price(value):
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError):
            return None

    top = spin.get("denom")
    if top is not None:
        return price(top)

    for event in reversed(spin.get("events") or []):
        if event.get("event") == DENOM_EVENT and event.get("denom") is not None:
            return price(event["denom"])
    return None


# -- the game's blocks -----------------------------------------------------

def configured_denoms(game: dict) -> dict[str, dict]:
    """`{canonical denomination: block}` for the active game, keyed the way a lookup compares them.

    Two config keys that canonicalise to one denomination is refused rather than resolved: "$1" and
    "$1.00" written as separate blocks is one denomination with two paytables, and picking either
    would be arbitrary.
    """
    blocks = (game or {}).get(DENOMS_KEY) or {}
    if not isinstance(blocks, dict):
        raise PaylineError(
            f"games.{game.get('process')}.{DENOMS_KEY} is {blocks!r}; it must be an object keyed "
            f"by the denomination as it is written on screen, e.g. "
            f"{{\"1c\": {{\"paylines\": \"40_line\", \"reel_strips\": {{...}}}}}}")

    out: dict[str, dict] = {}
    for key, block in blocks.items():
        canonical = normalize(key)
        if canonical in out:
            raise PaylineError(
                f"games.{game.get('process')}.{DENOMS_KEY} has two entries that mean the same "
                f"denomination ({canonical!r}). \"$1\", \"$1.00\" and \"1$\" are one denomination, "
                f"so one of them has to go -- there is no way to tell which paytable was meant")
        out[canonical] = block or {}
    return out


def resolve_set(game: dict, name: str, where: str) -> tuple[list, str | None, str | None]:
    """`(lines, label, note)` for a named payline set, raising and listing the names it has."""
    sets = (game or {}).get(SETS_KEY) or {}
    process = (game or {}).get("process")
    if not isinstance(sets, dict) or not sets:
        raise PaylineError(
            f"{where} names the payline set {name!r}, but games.{process} has no \"{SETS_KEY}\". "
            f"Add one, keyed by set name, each holding "
            f"{{\"label\": \"...\", \"lines\": [{{\"id\", \"name\", \"cells\"}}, ...]}}")
    if name not in sets:
        raise PaylineError(
            f"{where} names the payline set {name!r}, which games.{process}.{SETS_KEY} does not "
            f"have -- it has: {', '.join(sorted(sets)) or 'none'}. The set decides how many lines "
            f"this denomination pays, so there is nothing here that is safe to use instead")

    block = sets[name] or {}
    if not isinstance(block, dict) or not isinstance(block.get("lines"), list):
        raise PaylineError(
            f"games.{process}.{SETS_KEY}.{name} is {block!r}; it must be an object with a "
            f"\"lines\" list of {{\"id\", \"name\", \"cells\"}} entries")
    return block["lines"], block.get("label"), block.get("note")


def denom_strips(game: dict, denom: str, block: dict) -> dict:
    """The `reel_strips` block for one denomination, refusing to reach for the game's own.

    Same rule and same reason as `reelstrips.strips_for` refusing another game's sheet: each
    denomination here has its own reel layout, and mapping a 1c spin's stops through the $1 sheet
    does not fail, it names symbols confidently. `2c` and `1c` shipping identical strips today does
    not make either one the other's default.
    """
    strips = block.get("reel_strips")
    if not strips:
        raise PaylineError(
            f"games.{game.get('process')}.{DENOMS_KEY}.{denom} has no \"reel_strips\", so a spin "
            f"at this denomination has no paytable to map its reel stops through. Add "
            f"\"reel_strips\": {{\"path\": \"assets/paytable_excel_{denom}.xlsx\", "
            f"\"placeholders\": [...]}} -- each denomination has its own reel layout, so this will "
            f"not fall back to the game's own sheet")
    return strips


def _compare_with_log(canonical: str, logged: Decimal | None) -> str | None:
    """The sentence to put on the record when `denom.json` and the game's log disagree, or None.

    Reports; never decides. `denom.json` is the source by design, and this exists so a file that has
    been left behind cannot be believed *quietly* -- one fixed path holds one value for the whole
    cabinet, and the cabinet's denomination changes.
    """
    if logged is None:
        return None
    priced = cents_of(canonical)
    if priced is None or priced == logged:
        return None
    return (f"{DENOM_FILE_NAME} says {canonical} ({priced} cents) but this spin's own log recorded "
            f"{logged} cents. The file decides, so this audit walked {canonical}'s lines -- but one "
            f"of the two is describing a different spin. {DENOM_FILE_NAME} holds one value for the "
            f"whole cabinet, so check it was rewritten for this spin")


# -- the one call the runner makes -----------------------------------------

def select(cfg: dict, run_dir: str | None = None, settings: dict | None = None) -> DenomSelection:
    """Which payline set and paytable this audit should use, and where that came from.

    Reads `server/denom.json`. Returns the base selection -- reported, never silent -- when that file
    is not there, or when the game declares no denominations. Raises when the file is malformed, and
    when the game *does* declare denominations and the one in the file is not among them.
    """
    if settings and settings.get(RETIRED_SETTING):
        # Loud rather than silently ignored: the path is fixed now, so a setting pointing somewhere
        # else is a file someone is maintaining that nothing reads.
        LOG.warning("payline.%s (%s) is not read -- the denomination is read from the one fixed "
                    "path, %s", RETIRED_SETTING, settings[RETIRED_SETTING], DENOM_FILE)

    game = (cfg or {}).get("game") or {}
    process = game.get("process")
    if not process:
        raise PaylineError(
            "there is no active game, so there is no way to tell whose denominations these are. "
            "Set \"active\" in game_config.json to the running game's executable name")

    # The game's own value, for comparison only. Read first so it can be reported even when there is
    # no denom.json to compare it against.
    logged = logged_denom(read_spin(run_dir))

    block = read_denom_file()
    if block is None:
        return base_selection(
            f"{DENOM_FILE} does not exist, so nothing said which denomination was played -- the "
            f"game's own payline set and paytable were read. Write it there, holding "
            f"{{\"denom\": {{\"text\": \"1c\", ...}}}}", logged)

    raw = str(block["text"]).strip()
    canonical = normalize(raw)
    available = [str(d) for d in (block.get("available") or [])]

    blocks = configured_denoms(game)
    if not blocks:
        # The block's presence is this game's statement that denominations change what pays. Without
        # one there is nothing per-denomination to choose, so this is an answer and not a shortfall.
        return base_selection(
            f"{DENOM_FILE_NAME} says {raw!r}, and games.{process} declares no \"{DENOMS_KEY}\", so "
            f"the game's own payline set and paytable were read", logged)

    if canonical not in blocks:
        raise PaylineError(
            f"{DENOM_FILE} says the denomination is {raw!r}, which games.{process}.{DENOMS_KEY} has "
            f"no block for -- it has: {', '.join(sorted(blocks))}. A denomination decides how many "
            f"lines pay and which paytable the reel stops map through, so this will not fall back "
            f"to another one: it would walk the wrong number of lines over the right pixels")

    denom_block = blocks[canonical]
    where = f"games.{process}.{DENOMS_KEY}.{canonical}"
    set_name = denom_block.get("paylines")
    if not set_name or not isinstance(set_name, str):
        raise PaylineError(
            f"{where} has no \"paylines\" naming which set of lines this denomination pays. Set it "
            f"to one of the names in games.{process}.{SETS_KEY}")

    lines, label, note = resolve_set(game, set_name, where)
    strips = denom_strips(game, canonical, denom_block)

    # Every denomination the reader says the cabinet offers ought to have a block, or the next time
    # someone plays that one this raises. Said once, here, rather than found at the cabinet.
    unconfigured = [d for d in available if normalize(d) not in blocks]
    if unconfigured:
        LOG.warning("%s lists %s as available, and games.%s.%s has no block for %s -- a spin at "
                    "%s will fail rather than be audited",
                    DENOM_FILE_NAME, ", ".join(unconfigured), process, DENOMS_KEY,
                    "them" if len(unconfigured) > 1 else "it",
                    " or ".join(unconfigured))

    disagreement = _compare_with_log(canonical, logged)
    if disagreement:
        LOG.warning("%s", disagreement)

    LOG.info("denom %s (%s, from %s) -> payline set %r (%d lines), paytable %s",
             canonical, raw, DENOM_FILE_NAME, set_name, len(lines), strips.get("path"))
    return DenomSelection(
        text=canonical, raw=raw, value=block.get("value"), unit=block.get("unit"),
        available=available,
        source=f"{DENOM_FILE_NAME}, which says {raw!r}",
        logged=logged, disagreement=disagreement,
        set_name=set_name, set_label=label, note=note, lines=lines,
        strips_block=strips, strips_where=f"{where}.reel_strips")
