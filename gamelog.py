#!/usr/bin/env python
"""Game events, read live out of the game's own log.

`HuffNPuffLink.exe` logs both its server and client threads to
`C:\\logs\\Game\\HuffNPuffLink\\Logs\\HuffNPuffLink_Theme.log` at INFO, which makes it an oracle
for what the game is actually doing -- including the one thing the capture loop most needs:
the moment the reels come to rest.

That replaces a fixed sleep. Measured on this machine, an ordinary spin runs 3.3 s from press to
game over, while a Hold & Spin feature ran 53 s across 23 free spins. No single delay is right
for both, so the game is asked instead of guessed.

The markers below are copied from real lines. The log is verbose -- ~500 lines during a spin,
most of it progressive broadcasts -- so matching is deliberately narrow.

    python gamelog.py --watch          # name each event as the game emits it
    python gamelog.py --state          # what the game (and so the deck) is doing right now
    python gamelog.py --replay 12:06   # parse a window already on disk, for checking markers
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime

import logtail

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = logging.getLogger("spin.gamelog")

DEFAULT_LOG = r"C:\logs\Game\HuffNPuffLink\Logs\HuffNPuffLink_Theme.log"

# What ends a spin, from the capture loop's point of view: the outcome is on screen and the game
# is waiting for input again.
#
# `win` has to be in here. A win leaves the game parked on the collect/gamble offer and it does
# not emit `game_over` until that is resolved -- and the thing that resolves it is the *next*
# press, because Repeat Bet reads Collect Win in that state and means "collect, then bet again".
# So one press really does advance one spin, and waiting for `game_over` on a winning spin would
# mean waiting for a press this loop hasn't made yet. Measured: pressing to collect separately
# started an extra spin and put the loop one behind for the rest of the run.
TERMINAL = ("game_over", "win")

# "08/05/26 12:06:37.163 19 HuffNPuffLink:19540 INF: ..." -- the game's own clock, which is what
# event times should be reported in rather than when we happened to read the line.
_STAMP_RE = re.compile(r"^(\d\d/\d\d/\d\d \d\d:\d\d:\d\d\.\d\d\d)\s")
_STAMP_FORMAT = "%m/%d/%y %H:%M:%S.%f"

# Ordered: the first pattern that matches a line wins, so put the specific ones first.
EVENTS: list[tuple[str, re.Pattern]] = [
    # -- what the player set up before spinning
    #    `Did denom Change[True]` is the discriminator: the same line is logged on every spin
    #    with [False], so matching UpdateDenom alone would call every spin a denom change. Note
    #    it spells out True/False here while the rest of the log uses [T]/[F].
    ("denom_changed", re.compile(
        r"\[WagerGameApp\.UpdateDenom\] New denom\[(?P<denom>[\d.]+)\] Did denom Change\[True\]")),
    #    reasonForChange[Attract] is the attract loop cycling bets by itself, not the player.
    ("bet_changed", re.compile(
        r"\[Game\.BetConfigurationChanged\] betChangedFlags\[(?P<changed>[^\]]+)\] "
        r"reasonForChange\[Player\]")),

    # -- the spin itself
    ("bet_locked", re.compile(
        r"\[GameEngine\.LockBet\] totalBetValue\[(?P<total_bet>[\d.]+)\].*?denom\[(?P<denom>[\d.]+)\]")),
    ("spin_started", re.compile(r"msg\[GDK\.Common\.ServerAPI\.SpinMsg\]")),
    ("reels_spinning", re.compile(
        r"StateMachine\[SlotGameStateMachine\] transitioned from \[stateSetup\] to \[stateSpin\]")),
    # The reels are down. This is the marker that replaces the fixed delay.
    ("reels_stopped", re.compile(
        r"\[SlotGameEngine\.HandleInternalSlotReelsStoppedMsg\] lastStops\[(?P<stops>[\d ]+)\]")),

    # -- what landed. This game has no wilds or scatters; the equivalents are a mystery-symbol
    #    reveal and Cash-on-Reels coin symbols.
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
    ("jackpot_celebration", re.compile(
        r"\[ProgressiveFeature\.CreateCelebrationWinInfoAndPay\]")),

    # -- money. A win puts the gamble/collect offer up, which is also the moment the i-Deck
    #    relabels Repeat Bet to Collect Win. Verified both ways: it fires 0.6 s after the reels
    #    stop on the spin that paid 300.00, and not at all on three spins that paid nothing.
    #
    #    Two markers that look like wins and are not, both ruled out by measurement:
    #      [GameStateMachine.PayWin]  -- an unconditional state-machine step, logged exactly
    #          once per spin win or lose (163 of them against 163 GameOverMsg).
    #      SyncWinAmountMessage       -- redraws the WIN meter, so it also fires while idle when
    #          the denom changes; seen twice between two spins, belonging to neither.
    ("win", re.compile(
        r"StateMachine\[GambleOfferStateMachine\] transitioned from \[\w+\] to \[offerState\]")),
    ("progressive_level", re.compile(
        r"\[ProgressiveFeature\.EvaluateCurrentResults\] win level\[(?P<level>\d+)\]")),

    # -- which button the player chose once the win was offered. Both leave `playDecisionState`,
    #    so the destination is the answer. These must be matched before the general
    #    `gamble_state` rule below, which would otherwise swallow the same lines.
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

    # -- the i-Deck. The panel's labels are never logged, but the game logs every relabel and
    #    the state machines that decide them, which is what makes --state possible.
    ("deck_changed", re.compile(r"BetButtonPanelLayout\.ButtonPanelStateChanged")),
    ("idle_state", re.compile(
        r"StateMachine\[IdleStateMachine\] transitioned from \[\w+\] to \[(?P<state>\w+)\]")),
    ("gamble_state", re.compile(
        r"StateMachine\[GambleOfferStateMachine\] transitioned from \[\w+\] to \[(?P<state>\w+)\]")),
]

# What the game's idle state means for the deck. The names are the game's own; the descriptions
# are what the panel offers in each, and are the reason --state is useful without reading pixels.
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


class GameLogError(RuntimeError):
    pass


class Event:
    __slots__ = ("name", "at", "fields", "line")

    def __init__(self, name: str, at: datetime | None, fields: dict, line: str):
        self.name = name
        self.at = at
        self.fields = fields
        self.line = line

    @property
    def stops(self) -> list[int] | None:
        raw = self.fields.get("stops")
        return [int(n) for n in raw.split()] if raw else None

    def __repr__(self) -> str:
        extra = " ".join(f"{k}={v}" for k, v in self.fields.items())
        when = self.at.strftime("%H:%M:%S.%f")[:-3] if self.at else "?"
        return f"{when} {self.name}" + (f" ({extra})" if extra else "")


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
            events.append(Event(name, at, fields, line.rstrip()))
            break  # one event per line
    return events


class GameLogWatcher:
    """Turns the game's log into events, from a mark forward."""

    def __init__(self, path: str = DEFAULT_LOG):
        self.path = path
        if not os.path.isfile(path):
            raise GameLogError(
                f"the game log {path} does not exist, so spin events cannot be read. Set "
                "\"gamelog.path\" in config.json, or pass --no-gamelog to fall back to a "
                "fixed delay.")
        self._tail = logtail.LogTail(path)
        self.mark()

    def mark(self) -> None:
        """Note where the log ends. Call before triggering the spin."""
        self._tail.mark()

    def poll(self) -> list[Event]:
        """Events logged since the last mark/poll, in order."""
        return _parse(self._tail.read_new())

    def wait_for(self, names, timeout: float) -> Event | None:
        """The first event with one of these names, or None if none arrives in time."""
        wanted = {names} if isinstance(names, str) else set(names)
        for event in self.drain(timeout, timeout):
            if event.name in wanted:
                return event
        return None

    def drain(self, idle_timeout: float, ceiling: float, interval: float = 0.05):
        """Yield events as they appear, until the game goes quiet or the ceiling is hit.

        The runner's main primitive: a screenshot can be taken from inside the loop, so a
        mid-spin moment (reels stopped, feature triggered) is caught while the spin continues.
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

    Reads history rather than waiting for the next change, so it answers immediately even if
    the game has been sitting idle.
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
    state["deck"] = (GAMBLE_MODES.get(state["gamble"])
                     or DECK_MODES.get(state["idle"], "unknown"))
    return state


# -- CLI -------------------------------------------------------------------


def _configured_path(config: str) -> str:
    try:
        with open(config, encoding="utf-8") as fh:
            return json.load(fh).get("gamelog", {}).get("path") or DEFAULT_LOG
    except (OSError, ValueError):
        return DEFAULT_LOG


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", default=os.path.join(HERE, "config.json"))
    parser.add_argument("--log", help="game log to read (default: from config.json)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--watch", action="store_true", help="name each event as it happens")
    mode.add_argument("--state", action="store_true", help="what the game is doing right now")
    mode.add_argument("--replay", metavar="HH:MM",
                     help="parse events already logged in that minute, without waiting")
    args = parser.parse_args(argv)

    path = args.log or _configured_path(args.config)
    try:
        if args.state:
            state = current_state(path)
            print(f"game log:   {path}")
            print(f"as of:      {state['at'] or 'no state transitions found'}")
            print(f"idle state: {state['idle']}")
            print(f"gamble:     {state['gamble']}")
            print(f"feature:    {state['feature'] or '-'}")
            print(f"last stops: {state['last_stops'] or '-'}")
            print(f"deck:       {state['deck']}")
            return 0

        if args.replay:
            text = logtail.LogTail(path).tail(64 * 1024 * 1024)
            hits = [e for e in _parse(text)
                    if e.at and e.at.strftime("%H:%M").startswith(args.replay)]
            if not hits:
                print(f"no events logged in {args.replay}", file=sys.stderr)
                return 1
            for event in hits:
                print(event)
            span = (hits[-1].at - hits[0].at).total_seconds()
            print(f"\n{len(hits)} events spanning {span:.3f}s")
            return 0

        watcher = GameLogWatcher(path)
        print(f"watching {path} -- Ctrl+C to stop", flush=True)
        while True:
            for event in watcher.poll():
                print(event, flush=True)
            time.sleep(0.05)
    except GameLogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
