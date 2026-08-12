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

Some of these markers describe what the game *did*; a few describe what the player *asked for*
(`bet_button`, `bet_config_changed`, `denom_changed`, `touch`, and the `via` field on the state
transitions). Those exist for watch.py, which has no press of its own to time from and has to
recognise a manual action from the log alone. They are also the only place the cabinet records
that a human touched the screen rather than the button deck.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime

from . import logtail

DEFAULT_LOG = r"C:\logs\Game\HuffNPuffLink\Logs\HuffNPuffLink_Theme.log"

# What ends a spin: the outcome is on screen and the game is waiting for input again.
#
# `win` has to be in here. A win leaves the game parked on the collect/gamble offer and it does
# not emit `game_over` until that is resolved -- and the thing that resolves it is the *next*
# press, because Repeat Bet reads "Collect Win" in that state and means "collect, then bet
# again". So waiting for `game_over` on a winning spin would mean waiting for a press this script
# is never going to make.
TERMINAL = ("game_over", "win")

# What says the meters have stopped moving, once a terminal event has been reached.
#
# `win` is logged at the *start* of the win meter's count-up, not the end of it, so it is the one
# terminal event that fires while the screen is still changing. Measured over 520 rounds in the two
# logs on this machine, `win` -> `results_done` is 0.33 s median but 6.3 s at p90 and 44.8 s at
# worst -- so 28% of winning spins were shot before the meter had finished, and one real run
# recorded a win of **1.49** on a spin that paid **12.00** (run 2026-08-10_163826: `win` at
# 16:38:32.987, after shot at 16:38:33.826, meters settled at 16:38:39.300).
#
# Ordered most specific first, and both are here because they answer different halves:
#   win_bang_done -- the count-up itself finishing. 115/115 winning rounds, 6/405 losing ones.
#   results_done  -- the whole results presentation finishing. 519/520 rounds, win or lose, and
#       never earlier than win_bang_done (0-18 ms after it). That makes it the marker that
#       answers "are the meters settled" for a losing spin too, which is what `win_bang_done`
#       alone cannot do.
#
# `game_over` needs none of this: on a loss it already lands *after* `results_done` (0.17 s
# median, 404/404 losing rounds), so a spin that ends the ordinary way is settled by definition.
SETTLED = ("win_bang_done", "results_done")

# "08/05/26 12:06:37.163 19 HuffNPuffLink:19540 INF: ..." -- the game's own clock, which is what
# event times should be reported in rather than when we happened to read the line.
_STAMP_RE = re.compile(r"^(\d\d/\d\d/\d\d \d\d:\d\d:\d\d\.\d\d\d)\s")
_STAMP_FORMAT = "%m/%d/%y %H:%M:%S.%f"

# Ordered: the first pattern that matches a line wins, so the specific ones come first.
EVENTS: list[tuple[str, re.Pattern]] = [
    # -- what the player asked for. These are the log's only record of a human doing something,
    #    and each is a line shape no other rule here matches, so their order doesn't matter.
    #
    #    `bet_config_changed` is the good one: it names what changed *and* who changed it.
    #    reasonForChange[Attract] is the cabinet cycling denominations to itself while nobody is
    #    playing (8 of them in one log against 41 Player ones), so the reason has to be read
    #    before treating this as an action.
    ("bet_config_changed", re.compile(
        r"\[Game\.BetConfigurationChanged\] betChangedFlags\[(?P<flags>[^\]]*)\] "
        r"reasonForChange\[(?P<reason>\w+)\]")),
    ("bet_button", re.compile(r"\[BetManager\.HandleBetButtonPressed\]")),
    #    The touchscreen. Nothing else in either log knows a touch happened, so without this a
    #    collect or a Hold & Spin start made by hand is invisible until its consequence lands --
    #    and `gameclick.verdict` loses the only thing that separates "delivered onto dead space"
    #    from "never delivered at all", which is the difference between a wrong coordinate and a
    #    wrong click method.
    #
    #    Two line shapes for the one GDK message, because different games on this platform log it
    #    differently and there is no way to tell from the outside which a game writes. Counted
    #    over FortuneOx_Client.log: **0** of the first shape and 36 of the second, so with only
    #    the first this event never fires for that game and every failed click reads as silence.
    #    Both spell `GDK.Common.ServerAPI.TouchMsg` out in full, which is what keeps them off the
    #    80 `CreditMeterTouchMsg` lines in the same file -- a touch of the credit meter is not a
    #    touch of a widget. Verified against that log: 36 matches, none of them a CreditMeter
    #    line, and no other rule in this list matches any of the 36.
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

    # -- features. `feature` names which one: CoinOnReelFS is the Hold & Spin free spins,
    #    FreeSpinBonus the ordinary ones, SuperFreeSpinBonus the upgraded round.
    #    The bonus is announced the instant the reels stop, and then a long intro presentation
    #    plays before `feature_triggered`: measured at 27.7 s and 68.4 s on this machine, with
    #    the log genuinely silent for 29 s of it. Nothing else says a feature is coming, so
    #    without this a watcher has no way to tell that silence from a finished spin.
    #    Verified 1:1 against `feature_triggered` in both logs here (8 and 8, 1 and 1). Note the
    #    negative case `NoBonusTriggerMsg` is logged on every ordinary spin (855 of them) -- the
    #    `ServerAPI\.` prefix is what keeps this from matching it.
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

    # -- inside the gamble. GambleStateMachine is the double-up round itself and is a different
    #    machine from GambleOfferStateMachine below, which only offers it; nothing here collides
    #    with the `gamble_state` rule. The pick is the player's -- the game names the card they
    #    chose -- and is the only thing in the round a person does.
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

    # -- the meters catching up. See SETTLED above for why the after shot waits for this and what
    #    it read when it didn't. `stateResultsWithInterrupt` is the presentation being allowed to
    #    be cut short by a press; the transition out of it is the game saying the results display
    #    is finished, whether it ran to the end or was interrupted.
    #
    #    Anchored to `GameStateMachine` on purpose. `stateResultsDone` also appears twice per
    #    free-spin round on the FreeSpinStateMachines, which is a different thing entirely -- the
    #    feature finishing its own results, mid-spin.
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
    #    `via` is the message that caused the transition, and it is how a state change is traced
    #    back to the input that caused it -- SpinButtonMsg, BetValueButtonMsg and
    #    MaxBetButtonMsg are the deck; double_up_offer_decline/accept is the touchscreen
    #    answering the collect/gamble offer. It is captured as an optional field rather than as
    #    a rule of its own on purpose: a separate rule would match these same lines first and
    #    current_state() would stop seeing the transitions it reads the deck's mode from.
    ("idle_state", re.compile(
        r"StateMachine\[IdleStateMachine\] transitioned from \[(?P<from_state>\w+)\] "
        r"to \[(?P<state>\w+)\](?: on event \[(?P<via>[\w.]+)\])?")),
    ("gamble_state", re.compile(
        r"StateMachine\[GambleOfferStateMachine\] transitioned from \[(?P<from_state>\w+)\] "
        r"to \[(?P<state>\w+)\](?: on event \[(?P<via>[\w.]+)\])?")),

    #    The win meter's count-up finishing. Below `idle_state` deliberately, and this is the one
    #    rule in this list whose position costs something. Of the 126 lines in the two logs here
    #    that match it, 125 are `IdleStateMachine [Non-queued] [WinBangDone] not handled by state
    #    [statePlaying]` and collide with nothing -- but one was `IdleStateMachine transitioned
    #    from [stateBangup] to [stateDisabled] on event [WinBangDone]`, and matching that first
    #    would hide an idle transition from current_state(), which is the one thing this list must
    #    never do. So that line is read as `idle_state` and this event is missed on it. Nothing
    #    breaks: `results_done` above is what the after shot waits for, and it fires either way.
    #
    #    `\[WinBangDone\]` and not `WinBangDone`: the leading bracket is what keeps it off the 181
    #    `[FreeSpinWinBangDone_<feature>]` lines, which are one count-up per *free spin* inside a
    #    feature rather than the spin's own.
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

    def __init__(self, path: str = DEFAULT_LOG):
        self.path = path
        if not os.path.isfile(path):
            raise GameLogError(
                f"the game log {path} does not exist, so there is no way to tell when a spin "
                "has finished. Set \"gamelog.path\" in config.json.")
        self._tail = logtail.LogTail(path)
        self._pending: list[Event] = []
        self.mark()

    def mark(self) -> None:
        """Note where the log ends. Call before triggering the spin."""
        self._pending.clear()
        self._tail.mark()

    def _fill(self) -> None:
        """Read the log and queue whatever it has added.

        The queue is what makes it safe for a caller to `break` out of `drain` and then start a
        second one -- which is exactly what waiting for SETTLED past a terminal event does. One
        read of the log yields a *batch* of events while the byte offset advances past all of
        them, so a caller that stopped part-way through a batch used to lose the remainder for
        good. `win` and `results_done` are 43 ms apart at their closest, well inside one 50 ms
        poll, which makes the event the second drain is waiting for the one most likely to have
        been in the discarded remainder.
        """
        self._pending.extend(_parse(self._tail.read_new()))

    def poll(self) -> list[Event]:
        """Events logged since the last mark/poll, in order."""
        self._fill()
        batch, self._pending = self._pending, []
        return batch

    def drain(self, idle_timeout: float, ceiling: float, interval: float = 0.05):
        """Yield events as they appear, until the game goes quiet or the ceiling is hit.

        Stopping is the caller's decision -- it `break`s when it has what it wants. Deciding
        here instead looks tidier and is wrong: the caller sometimes needs to *ignore* an event
        it would otherwise stop on, and a generator that has already returned cannot be resumed.

        Events are taken off `_pending` one at a time rather than out of a local batch, so a
        caller that breaks part-way through leaves the rest queued for the next drain instead of
        dropping them -- see `_fill`.

        Two limits, because one number cannot serve both cases. `idle_timeout` is the real one:
        it restarts on every event, so a feature that keeps emitting events is followed for as
        long as it runs, while a game that has genuinely gone quiet is not waited on. `ceiling`
        is only a backstop against a game that emits forever. A flat total timeout was tried
        first and cut a Hold & Spin off mid-feature after 8 free spins.
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


def current_state(path: str = DEFAULT_LOG, limit: int = 512 * 1024) -> dict:
    """What the game is doing now, from the state transitions already in the log.

    Reads history rather than waiting for the next change, so it answers immediately even if the
    game has been sitting idle for hours.
    """
    if not os.path.isfile(path):
        raise GameLogError(f"the game log {path} does not exist. Set \"gamelog.path\".")
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
