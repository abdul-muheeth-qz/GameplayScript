"""Game events, read live out of the game's own log -- the oracle for what the game is doing.

That is why there is no fixed delay anywhere in this stage: an ordinary spin runs 3.3 s from press
to game over while a Hold & Spin ran 53 s over 23 free spins, so no single number is right for both.

Two traps, both of which cost an afternoon. **The directory timestamp lies** -- the game holds the
handle open, so `LastWriteTime` read 11:04 while the file was being appended to at 14:31; judge
liveness by reading the tail. And **it rotates** at ~20 MB, which `logtail` handles; a reader holding
a byte offset would seek past the end and go quietly silent.

The markers are copied from real lines, not guessed, and matching is deliberately narrow -- the log
runs ~500 lines during a spin. A few describe what the *player* asked for (`bet_button`,
`bet_config_changed`, `denom_changed`, `touch`, and `via`); those are for watch.py, which has no
press of its own to time from, and they are the only record that a human touched the glass.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime

from . import logtail

# How long the log may go quiet before a spin is called over. It **restarts on every event**, which
# is what lets a 53 s Hold & Spin be followed to its end -- a flat total was tried and cut a feature
# off mid-way. A measurement, so it lives here rather than in config.json.
IDLE_TIMEOUT_S = 8.0

# What ends a spin. `win` has to be in here: a win parks the game on the collect/gamble offer and it
# does not log `game_over` until the next press resolves that -- a press this script never makes.
TERMINAL = ("game_over", "win")

# What says the meters have stopped moving, once a terminal event has been reached. Both are here
# because they answer different halves: `win_bang_done` is the count-up finishing (115/115 winning
# rounds, 6/405 losing), `results_done` the whole presentation (519/520 either way, and never
# earlier than win_bang_done), which is what answers for a losing spin too. Most specific first.
SETTLED = ("win_bang_done", "results_done")

# The game's own clock, which is what event times are reported in rather than when the line was read.
_STAMP_RE = re.compile(r"^(\d\d/\d\d/\d\d \d\d:\d\d:\d\d\.\d\d\d)\s")
_STAMP_FORMAT = "%m/%d/%y %H:%M:%S.%f"

# Ordered: the first pattern that matches a line wins, so the specific ones come first.
EVENTS: list[tuple[str, re.Pattern]] = [
    # -- what the player asked for. The log's only record of a human doing something, and each is a
    #    line shape no other rule matches, so their order does not matter. `bet_config_changed`
    #    names what changed *and* who changed it -- reasonForChange[Attract] is the cabinet cycling
    #    denominations to itself while nobody plays, so the reason must be read before acting on it.
    ("bet_config_changed", re.compile(
        r"\[Game\.BetConfigurationChanged\] betChangedFlags\[(?P<flags>[^\]]*)\] "
        r"reasonForChange\[(?P<reason>\w+)\]")),
    ("bet_button", re.compile(r"\[BetManager\.HandleBetButtonPressed\]")),
    #    The touchscreen, and nothing else in either log knows a touch happened -- without it a
    #    collect made by hand is invisible, and `gameclick.verdict` loses the only thing separating
    #    "delivered onto dead space" from "never delivered at all".
    #
    #    **Two line shapes for the one GDK message**, both load-bearing: different games log it
    #    differently, and FortuneOx writes 0 of the first against 36 of the second, so with only the
    #    first this event never fired for that game and every failed click read as silence. Both
    #    spell the message out in full, which keeps them off that file's 80 `CreditMeterTouchMsg`
    #    lines.
    ("touch", re.compile(r"\[GameSession\.MsgToServer\].*msg\[GDK\.Common\.ServerAPI\.TouchMsg\]"
                         r"|ServerProxy\.ClientToServerRequest: "
                         r"GDK\.Common\.ServerAPI\.TouchMsg")),

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

    # -- features. `feature` names which one. The bonus is announced the instant the reels stop and
    #    then a long intro plays before `feature_triggered` -- 27.7 s and 68.4 s here, with the log
    #    silent for 29 s of it -- and nothing else says a feature is coming, so without this a
    #    watcher cannot tell that silence from a finished spin. The `ServerAPI\.` prefix is what
    #    keeps it off `NoBonusTriggerMsg`, logged on all 855 ordinary spins.
    ("bonus_triggered", re.compile(r"msg\[GDK\.Common\.ServerAPI\.BonusTriggerMsg\]")),
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

    # -- the win. A win puts the gamble/collect offer up, which is also when the i-Deck relabels
    #    Repeat Bet to Collect Win. Verified both ways, and validated 5/5 against platform telemetry.
    #
    #    Two markers that look like wins and are not: `[GameStateMachine.PayWin]` is an
    #    unconditional step logged once per spin win or lose, and `SyncWinAmountMessage` redraws the
    #    WIN meter and so fires while idle.
    ("win", re.compile(
        r"StateMachine\[GambleOfferStateMachine\] transitioned from \[\w+\] to \[offerState\]")),
    ("progressive_level", re.compile(
        r"\[ProgressiveFeature\.EvaluateCurrentResults\] win level\[(?P<level>\d+)\]")),

    # -- inside the gamble. GambleStateMachine is the double-up round itself, a different machine
    #    from the GambleOfferStateMachine below that only offers it, so nothing here collides with
    #    the `gamble_state` rule. The pick is the player's and the only thing a person does here.
    ("gamble_pick", re.compile(
        r"StateMachine\[GambleStateMachine\] transitioned from \[waitForPickState\] to \[\w+\] "
        r"on event \[RED_BLACK_(?P<pick>\w+)_CARD\]")),
    ("gamble_result", re.compile(
        r"StateMachine\[GambleStateMachine\] transitioned from \[pickedState\] "
        r"to \[displayResultsState\]")),
    ("gamble_over", re.compile(
        r"StateMachine\[GambleStateMachine\] transitioned from \[displayResultsState\] "
        r"to \[waitForEndGameAnimState\]")),

    # -- which button resolved the win. Both leave `playDecisionState`, so the destination is the
    #    answer. These must be matched before the general `gamble_state` rule below, which would
    #    otherwise swallow the same lines.
    ("take_win", re.compile(
        r"StateMachine\[GambleOfferStateMachine\] transitioned from \[playDecisionState\] "
        r"to \[dontplaystate\]")),
    ("gamble_played", re.compile(
        r"StateMachine\[GambleOfferStateMachine\] transitioned from \[playDecisionState\] "
        r"to \[playState\]")),

    # -- the wager. `denom_changed` is logged on every activation as well as on a real change,
    #    hence the flag; `bet_changed` carries the new total, which is what a report wants to
    #    quote. Both land ~600 ms after the bet_config_changed above.
    ("denom_changed", re.compile(
        r"\[WagerGameApp\.UpdateDenom\] New denom\[(?P<denom>[\d.]+)\] "
        r"Did denom Change\[(?P<changed>\w+)\]")),
    ("bet_changed", re.compile(
        r"\[BetManager\.UpdateCurrentBet\]\[CurrentBet .*?TotalBetValue:(?P<total_bet>[\d.]+)")),

    # -- the meters catching up; see SETTLED above. `stateResultsWithInterrupt` is the presentation
    #    allowed to be cut short by a press, and the transition out of it is the game saying the
    #    results display is finished either way. Anchored to `GameStateMachine` on purpose:
    #    `stateResultsDone` also appears twice per free-spin round, which is a different thing.
    ("results_done", re.compile(
        r"StateMachine\[GameStateMachine\] transitioned from \[stateResultsWithInterrupt\] "
        r"to \[stateResultsDone\]")),

    # -- end of spin
    ("final_grid", re.compile(
        r"\[SlotGameEngine\.HandleGameOverForGameMode\] LastStops\[(?P<stops>[\d ]+)\]")),
    ("game_over", re.compile(r"msg\[GDK\.Common\.ServerAPI\.GameOverMsg\]")),
    ("new_game_allowed", re.compile(r"msg\[GDK\.Common\.ServerAPI\.NewGameAllowedMsg\]")),

    # -- the i-Deck. The panel's labels are never logged anywhere, but the game logs every
    #    relabel and the state machines that decide them, which is what makes current_state work.
    ("deck_changed", re.compile(r"BetButtonPanelLayout\.ButtonPanelStateChanged")),
    #    `via` is the message that caused the transition, which is how a state change is traced back
    #    to its input. An optional field rather than a rule of its own, deliberately: a separate rule
    #    would match these lines first and current_state() would stop seeing the transitions it reads
    #    the deck's mode from.
    ("idle_state", re.compile(
        r"StateMachine\[IdleStateMachine\] transitioned from \[(?P<from_state>\w+)\] "
        r"to \[(?P<state>\w+)\](?: on event \[(?P<via>[\w.]+)\])?")),
    ("gamble_state", re.compile(
        r"StateMachine\[GambleOfferStateMachine\] transitioned from \[(?P<from_state>\w+)\] "
        r"to \[(?P<state>\w+)\](?: on event \[(?P<via>[\w.]+)\])?")),

    #    The win meter's count-up finishing. **Below `idle_state` deliberately** -- the one rule here
    #    placed for its position rather than its specificity. 125 of the 126 matching lines collide
    #    with nothing, but one is an idle transition, and matching that first would hide it from
    #    current_state(), which is the thing this list must never do. That line reads as `idle_state`
    #    and this event is missed on it; nothing breaks, because `results_done` is what the wait
    #    stops on. `\[WinBangDone\]` and not `WinBangDone`, to stay off the 181
    #    `[FreeSpinWinBangDone_*]` lines -- one count-up per free spin inside a feature.
    ("win_bang_done", re.compile(r"\[WinBangDone\]")),

    # -- the process itself. A denomination change reloads the game's scene, and occasionally the
    #    whole client restarts, which gives OBS a new window to capture.
    ("game_started", re.compile(r"-{5,} (?P<theme>\S+) Client Start -{5,}")),
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
    "bet_button": "a bet button was pressed",
    "touch": "the screen was touched",
    "spin_started": "spin started",
    "reels_spinning": "reels spinning",
    "mystery_reveal": "mystery symbol revealed",
    "bonus_triggered": "a bonus was triggered -- its intro is playing",
    "cash_symbol": "a cash-on-reels coin symbol landed",
    "hold_and_spin_prompt": "Hold & Spin waiting to be started",
    "hold_and_spin_started": "Hold & Spin started",
    "wager_saver_offered": "wager saver offered",
    "wager_saver_accepted": "wager saver accepted",
    "jackpot_awarded": "JACKPOT awarded",
    "jackpot_celebration": "jackpot celebration",
    "win": "WIN -- collect/gamble offered, and the deck now reads Collect Win",
    "win_bang_done": "the win meter finished counting up",
    "results_done": "the results display finished -- the meters have stopped moving",
    "take_win": "chose TAKE WIN",
    "gamble_played": "chose GAMBLE",
    "gamble_result": "the gamble round was decided",
    "gamble_over": "the gamble ended",
    "game_over": "spin complete",
    "new_game_allowed": "ready for another spin",
    "deck_changed": "the i-Deck relabelled its buttons",
}


class GameLogError(RuntimeError):
    pass


def path_for(game_cfg: dict) -> str:
    """The active game's log, out of its own block (`cfg["game"]`).

    **There is no default here, and there must not be one.** This was a module constant holding
    HuffNPuffLink's path, reached whenever a game block had no `log` -- and because that file
    exists on this cabinet, `GameLogWatcher` opened it happily and the whole stage then read its
    terminal events, its deck mode and its pending-win carry off *another game's* spins while
    reporting success. Every other per-game value refuses to guess for the same reason
    (`gameclick.targets_for`, `payline.geometry_for`, `roi.locate_meter_roi`); the log was the last
    one that did not.

    `payline.telemetry.game_logs` asks the same question of the same key and additionally globs the
    rotated siblings, so it stays separate: this one raises `GameLogError`, which is what
    `spin.run` catches.
    """
    path = (game_cfg or {}).get("log")
    if not path:
        process = (game_cfg or {}).get("process") or "the active game"
        raise GameLogError(
            f"the \"{process}\" block in game_config.json has no \"log\", so there is no way to "
            f"tell when a spin has finished -- and no other game's log is safe to read instead, "
            f"since it would report that game's spins as this one's. Set "
            f"games[\"{process}\"].log to the file this game writes.")
    return path


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
    if event.name == "bet_config_changed":
        who = event.fields.get("reason")
        return (f"bet configuration changed ({event.fields.get('flags')}), "
                + ("by the player" if who == "Player" else f"reason {who}"))
    if event.name == "denom_changed":
        changed = event.fields.get("changed") == "True"
        return (f"denomination {'changed to' if changed else 'is'} {event.fields.get('denom')}")
    if event.name == "bet_changed":
        return f"bet now {event.fields.get('total_bet')}"
    if event.name == "gamble_pick":
        return f"gambled on {str(event.fields.get('pick', '')).lower()}"
    if event.name == "game_started":
        return f"{event.fields.get('theme')} client started"
    if event.name in ("idle_state", "gamble_state"):
        return (f"{event.name} -> {event.fields.get('state')}"
                + (f" (on {event.fields['via']})" if event.fields.get("via") else ""))
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

    def __init__(self, path: str):
        self.path = path
        if not os.path.isfile(path):
            raise GameLogError(
                f"the game log {path} does not exist, so there is no way to tell when a spin "
                "has finished. Check games.<exe>.log in game_config.json against the file the "
                "running game actually writes.")
        self._tail = logtail.LogTail(path)
        self._pending: list[Event] = []
        self.mark()

    def mark(self) -> None:
        """Note where the log ends. Call before triggering the spin."""
        self._pending.clear()
        self._tail.mark()

    def _fill(self) -> None:
        """Read the log and queue whatever it has added.

        **The queue must not be inlined back into `poll`.** It is what makes it safe to `break` out
        of `drain` and start a second one, which is what waiting for SETTLED does: one read parses a
        *batch* while the byte offset advances past all of it, so a caller stopping part-way used to
        lose the remainder -- and `win` and `results_done` are 43 ms apart at their closest, well
        inside one poll.
        """
        self._pending.extend(_parse(self._tail.read_new()))

    def poll(self) -> list[Event]:
        """Events logged since the last mark/poll, in order."""
        self._fill()
        batch, self._pending = self._pending, []
        return batch

    def drain(self, idle_timeout: float, ceiling: float, interval: float = 0.05):
        """Yield events as they appear, until the game goes quiet or the ceiling is hit.

        Stopping is the caller's decision. Deciding here looks tidier and is wrong: the caller
        sometimes needs to *ignore* an event it would otherwise stop on, and a returned generator
        cannot be resumed. Events come off `_pending` one at a time, so breaking part-way leaves the
        rest queued -- see `_fill`.

        `idle_timeout` is the real limit and **restarts on every event**, so a feature that keeps
        talking is followed for as long as it runs; `ceiling` is only a backstop. A flat total was
        tried first and cut a Hold & Spin off mid-feature after 8 free spins.
        """
        started = time.monotonic()
        quiet_until = started + idle_timeout
        while True:
            self._fill()
            while self._pending:
                yield self._pending.pop(0)
                quiet_until = time.monotonic() + idle_timeout
            now = time.monotonic()
            if now >= quiet_until or now - started >= ceiling:
                return
            time.sleep(interval)


def current_state(path: str, limit: int = 512 * 1024) -> dict:
    """What the game is doing now, from the state transitions already in the log -- history rather
    than the next change, so it answers immediately after hours of idling."""
    if not os.path.isfile(path):
        raise GameLogError(f"the game log {path} does not exist. Check games.<exe>.log in "
                           f"game_config.json.")
    state = {"idle": None, "gamble": None, "feature": None, "last_stops": None, "at": None,
             "bet": None, "denom": None}
    for event in _parse(logtail.LogTail(path).tail(limit)):
        if event.name == "idle_state":
            state["idle"] = event.fields.get("state")
        elif event.name == "gamble_state":
            state["gamble"] = event.fields.get("state")
        elif event.name in ("bet_changed", "bet_locked"):
            state["bet"] = event.fields.get("total_bet")
            state["denom"] = event.fields.get("denom") or state["denom"]
        elif event.name == "denom_changed":
            state["denom"] = event.fields.get("denom")
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
