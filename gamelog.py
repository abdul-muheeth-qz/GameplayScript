"""Game events, read live out of the game's own log.

`HuffNPuffLink.exe` logs both its server and client threads to
`C:\\logs\\Game\\HuffNPuffLink\\Logs\\HuffNPuffLink_Theme.log` at INFO, which makes it an oracle
for what the game is actually doing -- including the one thing capturing a spin most needs: the
moment the spin is genuinely over.

That is why there is no fixed delay anywhere in this tool. Measured on this machine, an ordinary
spin runs 3.3 s from press to game over, while a Hold & Spin ran 53 s across 23 free spins. No
single number is right for both, so the game is asked instead of guessed.

Two traps in this file, both of which cost an afternoon to find:

  * **Its directory timestamp lies.** The game holds the handle open, so `LastWriteTime` read
    11:04 while the file was being appended to at 14:31. Judge liveness by reading the tail,
    never by `os.path.getmtime`.
  * **It rotates** at ~20 MB into `HuffNPuffLink_Theme-YYYYMMDD-HHMMSS.log`. `logtail` handles
    that; a reader holding a byte offset would seek past the end and go quietly silent.

The markers below are copied from real lines, not guessed. The log is verbose -- ~500 lines
during a spin, most of it progressive broadcasts -- so matching is deliberately narrow.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime

import logtail

DEFAULT_LOG = r"C:\logs\Game\HuffNPuffLink\Logs\HuffNPuffLink_Theme.log"

# What ends a spin: the outcome is on screen and the game is waiting for input again.
#
# `win` has to be in here. A win leaves the game parked on the collect/gamble offer and it does
# not emit `game_over` until that is resolved -- and the thing that resolves it is the *next*
# press, because Repeat Bet reads "Collect Win" in that state and means "collect, then bet
# again". So waiting for `game_over` on a winning spin would mean waiting for a press this script
# is never going to make.
TERMINAL = ("game_over", "win")

# "08/05/26 12:06:37.163 19 HuffNPuffLink:19540 INF: ..." -- the game's own clock, which is what
# event times should be reported in rather than when we happened to read the line.
_STAMP_RE = re.compile(r"^(\d\d/\d\d/\d\d \d\d:\d\d:\d\d\.\d\d\d)\s")
_STAMP_FORMAT = "%m/%d/%y %H:%M:%S.%f"

# Ordered: the first pattern that matches a line wins, so the specific ones come first.
EVENTS: list[tuple[str, re.Pattern]] = [
    # -- the spin itself
    ("bet_locked", re.compile(
        r"\[GameEngine\.LockBet\] totalBetValue\[(?P<total_bet>[\d.]+)\].*?"
        r"denom\[(?P<denom>[\d.]+)\]")),
    ("spin_started", re.compile(r"msg\[GDK\.Common\.ServerAPI\.SpinMsg\]")),
    ("reels_spinning", re.compile(
        r"StateMachine\[SlotGameStateMachine\] transitioned from \[stateSetup\] to \[stateSpin\]")),
    ("reels_stopped", re.compile(
        r"\[SlotGameEngine\.HandleInternalSlotReelsStoppedMsg\] lastStops\[(?P<stops>[\d ]+)\]")),

    # -- what landed. This game has no wilds or scatters (0 occurrences of either word); the
    #    equivalents are a mystery-symbol reveal and Cash-on-Reels coin symbols.
    ("mystery_reveal", re.compile(
        r"StateMachine\[MysterySymbolStateMachine\w*\].*to \[statePerformMysterySymbolReveal\]")),
    ("cash_symbol", re.compile(r"msg\[CashOnReels\.Common\.SymbolValueMsg\]")),

    # -- features. `feature` names which one: CoinOnReelFS is the Hold & Spin free spins,
    #    FreeSpinBonus the ordinary ones, SuperFreeSpinBonus the upgraded round.
    ("feature_triggered", re.compile(
        r"StateMachine\[FreeSpinStateMachine(?P<feature>\w+)\] transitioned from \[stateIdle\] "
        r"to \[stateStart\]")),
    ("hold_and_spin_prompt", re.compile(
        r"StateMachine\[HoldNSpinTouchToStart\] transitioned from \[statePrompt\] to \[stateWait\]")),
    #    This is the transition made *on* OLEDTouchToStartMsg -- the message only ever appears as
    #    a state-machine event, never as a `msg[...]` line, so matching the message form finds
    #    nothing.
    ("hold_and_spin_started", re.compile(
        r"StateMachine\[HoldNSpinTouchToStart\] transitioned from \[stateWait\] to \[stateTouched\]")),

    # -- wager saver: the free re-spin offered when the balance can't cover another bet.
    #    "Offering Wager Saver [F]" is logged on nearly every spin, so only [T] is the offer.
    ("wager_saver_offered", re.compile(r"Offering Wager Saver \[T\]")),
    ("wager_saver_accepted", re.compile(
        r"StateMachine\[WagerSaverStateMachine\] transitioned from \[decisionState\] "
        r"to \[spinSetup\]")),

    # -- jackpot / progressive award
    ("jackpot_awarded", re.compile(r"\[ProgressiveFeature\.AwardLevels\]")),
    ("jackpot_celebration", re.compile(r"\[ProgressiveFeature\.CreateCelebrationWinInfoAndPay\]")),

    # -- the win. A win puts the gamble/collect offer up, which is also the moment the i-Deck
    #    relabels Repeat Bet to Collect Win. Verified both ways: it fires 0.58 s after the reels
    #    stop on the spin that paid 300.00, and not at all on spins that paid nothing.
    #
    #    Two markers that look like wins and are not, both ruled out by measurement:
    #      [GameStateMachine.PayWin]  -- an unconditional state-machine step, logged exactly once
    #          per spin win or lose (163 of them against 163 GameOverMsg).
    #      SyncWinAmountMessage       -- redraws the WIN meter, so it also fires while idle when
    #          the denomination changes; seen twice between two spins, belonging to neither.
    ("win", re.compile(
        r"StateMachine\[GambleOfferStateMachine\] transitioned from \[\w+\] to \[offerState\]")),
    ("progressive_level", re.compile(
        r"\[ProgressiveFeature\.EvaluateCurrentResults\] win level\[(?P<level>\d+)\]")),

    # -- which button resolved the win. Both leave `playDecisionState`, so the destination is the
    #    answer. These must be matched before the general `gamble_state` rule below, which would
    #    otherwise swallow the same lines.
    ("take_win", re.compile(
        r"StateMachine\[GambleOfferStateMachine\] transitioned from \[playDecisionState\] "
        r"to \[dontplaystate\]")),
    ("gamble_played", re.compile(
        r"StateMachine\[GambleOfferStateMachine\] transitioned from \[playDecisionState\] "
        r"to \[playState\]")),

    # -- end of spin
    ("final_grid", re.compile(
        r"\[SlotGameEngine\.HandleGameOverForGameMode\] LastStops\[(?P<stops>[\d ]+)\]")),
    ("game_over", re.compile(r"msg\[GDK\.Common\.ServerAPI\.GameOverMsg\]")),
    ("new_game_allowed", re.compile(r"msg\[GDK\.Common\.ServerAPI\.NewGameAllowedMsg\]")),

    # -- the i-Deck. The panel's labels are never logged anywhere, but the game logs every
    #    relabel and the state machines that decide them, which is what makes current_state work.
    ("deck_changed", re.compile(r"BetButtonPanelLayout\.ButtonPanelStateChanged")),
    ("idle_state", re.compile(
        r"StateMachine\[IdleStateMachine\] transitioned from \[\w+\] to \[(?P<state>\w+)\]")),
    ("gamble_state", re.compile(
        r"StateMachine\[GambleOfferStateMachine\] transitioned from \[\w+\] to \[(?P<state>\w+)\]")),
]

# What the game's idle state means for the deck. The names are the game's own; the descriptions
# are what the panel offers in each, and are the reason the deck's mode can be reported without
# reading a single pixel.
DECK_MODES = {
    "stateIdleWithCredits": "idle with credits -- spin available",
    "stateWaitForPlay": "bet chosen, waiting for the spin press",
    "statePlaying": "spin in progress",
    "stateBangup": "counting up a win -- the spin button interrupts it",
    "stateIdleNoCredits": "no credits -- spin unavailable",
}
# Only offerState is a state the deck actually sits in; the decision states pass through in
# milliseconds on every spin, so reporting them would be noise.
GAMBLE_MODES = {
    "offerState": "a win is pending -- the spin button reads Collect Win, and the game waits "
                  "here until it is pressed",
}

# Plain English for the console and spin.json, so a timeline can be read without this file open.
NOTES = {
    "spin_started": "spin started",
    "reels_spinning": "reels spinning",
    "mystery_reveal": "mystery symbol revealed",
    "cash_symbol": "a cash-on-reels coin symbol landed",
    "hold_and_spin_prompt": "Hold & Spin waiting to be started",
    "hold_and_spin_started": "Hold & Spin started",
    "wager_saver_offered": "wager saver offered",
    "wager_saver_accepted": "wager saver accepted",
    "jackpot_awarded": "JACKPOT awarded",
    "jackpot_celebration": "jackpot celebration",
    "win": "WIN -- collect/gamble offered, and the deck now reads Collect Win",
    "take_win": "chose TAKE WIN",
    "gamble_played": "chose GAMBLE",
    "game_over": "spin complete",
    "new_game_allowed": "ready for another spin",
    "deck_changed": "the i-Deck relabelled its buttons",
}


class GameLogError(RuntimeError):
    pass


class Event:
    __slots__ = ("name", "at", "fields")

    def __init__(self, name: str, at: datetime | None, fields: dict):
        self.name = name
        self.at = at
        self.fields = fields

    @property
    def stops(self) -> list[int] | None:
        raw = self.fields.get("stops")
        return [int(n) for n in raw.split()] if raw else None

    def __repr__(self) -> str:
        extra = " ".join(f"{k}={v}" for k, v in self.fields.items())
        when = self.at.strftime("%H:%M:%S.%f")[:-3] if self.at else "?"
        return f"{when} {self.name}" + (f" ({extra})" if extra else "")


def describe(event: Event) -> str:
    """A one-line human reading of an event."""
    if event.name == "bet_locked":
        return f"bet locked at {event.fields.get('total_bet')} (denom {event.fields.get('denom')})"
    if event.name == "reels_stopped":
        return f"reels stopped at {event.fields.get('stops')}"
    if event.name == "final_grid":
        return f"final grid {event.fields.get('stops')}"
    if event.name == "feature_triggered":
        return f"feature started: {event.fields.get('feature')}"
    if event.name == "progressive_level":
        return f"progressive win level {event.fields.get('level')}"
    if event.name in ("idle_state", "gamble_state"):
        return f"{event.name} -> {event.fields.get('state')}"
    return NOTES.get(event.name, event.name)


def _parse(text: str) -> list[Event]:
    events = []
    for line in text.splitlines():
        for name, pattern in EVENTS:
            match = pattern.search(line)
            if not match:
                continue
            stamp = _STAMP_RE.match(line)
            at = None
            if stamp:
                try:
                    at = datetime.strptime(stamp.group(1), _STAMP_FORMAT)
                except ValueError:
                    pass
            fields = {k: v for k, v in (match.groupdict() or {}).items() if v is not None}
            events.append(Event(name, at, fields))
            break  # one event per line
    return events


class GameLogWatcher:
    """Turns the game's log into events, from a mark forward."""

    def __init__(self, path: str = DEFAULT_LOG):
        self.path = path
        if not os.path.isfile(path):
            raise GameLogError(
                f"the game log {path} does not exist, so there is no way to tell when a spin "
                "has finished. Set \"gamelog.path\" in config.json.")
        self._tail = logtail.LogTail(path)
        self.mark()

    def mark(self) -> None:
        """Note where the log ends. Call before triggering the spin."""
        self._tail.mark()

    def poll(self) -> list[Event]:
        """Events logged since the last mark/poll, in order."""
        return _parse(self._tail.read_new())

    def drain(self, idle_timeout: float, ceiling: float, interval: float = 0.05):
        """Yield events as they appear, until the game goes quiet or the ceiling is hit.

        Stopping is the caller's decision -- it `break`s when it has what it wants. Deciding
        here instead looks tidier and is wrong: the caller sometimes needs to *ignore* an event
        it would otherwise stop on, and a generator that has already returned cannot be resumed.

        Two limits, because one number cannot serve both cases. `idle_timeout` is the real one:
        it restarts on every event, so a feature that keeps emitting events is followed for as
        long as it runs, while a game that has genuinely gone quiet is not waited on. `ceiling`
        is only a backstop against a game that emits forever. A flat total timeout was tried
        first and cut a Hold & Spin off mid-feature after 8 free spins.
        """
        started = time.monotonic()
        quiet_until = started + idle_timeout
        while True:
            for event in self.poll():
                yield event
                quiet_until = time.monotonic() + idle_timeout
            now = time.monotonic()
            if now >= quiet_until or now - started >= ceiling:
                return
            time.sleep(interval)


def current_state(path: str = DEFAULT_LOG, limit: int = 512 * 1024) -> dict:
    """What the game is doing now, from the state transitions already in the log.

    Reads history rather than waiting for the next change, so it answers immediately even if the
    game has been sitting idle for hours.
    """
    if not os.path.isfile(path):
        raise GameLogError(f"the game log {path} does not exist. Set \"gamelog.path\".")
    state = {"idle": None, "gamble": None, "feature": None, "last_stops": None, "at": None}
    for event in _parse(logtail.LogTail(path).tail(limit)):
        if event.name == "idle_state":
            state["idle"] = event.fields.get("state")
        elif event.name == "gamble_state":
            state["gamble"] = event.fields.get("state")
        elif event.name == "win":
            # `win` matches the offerState line before `gamble_state` can, so put it back.
            state["gamble"] = "offerState"
        elif event.name == "feature_triggered":
            state["feature"] = event.fields.get("feature")
        elif event.name in ("reels_stopped", "final_grid"):
            state["last_stops"] = event.stops
        if event.at:
            state["at"] = event.at
    # A pending win wins over the idle state: that is what the deck is showing.
    state["deck"] = GAMBLE_MODES.get(state["gamble"]) or DECK_MODES.get(state["idle"], "unknown")
    return state
