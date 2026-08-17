"""What the player just did, worked out from the two logs.

spin.py knows what a spin is because it caused one. Nothing here has that luxury: the player presses
whatever they like, so this recognises the beginning of an action, the end of it, and what it was
from the log alone. Four questions, answered separately:

  * **Did an action just start?** `trigger_of` -- markers that only appear because a human did
    something. An i-Deck press is the other trigger and belongs to the caller, coming from the
    panel service's log rather than the game's.
  * **Is this moment worth a frame?** `MILESTONES` -- a Hold & Spin runs ~50 s over 20-odd free
    spins, so one shot at each end would miss the whole feature.
  * **Is it over, or is the game just waiting?** `Action.waiting_for`, and if waiting, whether for
    the *player* (unbounded, the game having no timeout either) or for its own presentation
    (bounded). A round is never closed and reopened.
  * **What was it?** `Action.summary` -- a list of kinds rather than one label, because one press is
    routinely a collect *and* a spin.

**No I/O and no clock beyond stamping records**, which is what lets this be checked by replaying
real log history instead of needing a cabinet.
"""

from __future__ import annotations

import re
from datetime import datetime

from . import gamelog

# Events that mean a person did something; one of these while nothing is being followed opens an
# action. Deliberately not "any event": the game logs transitions and relabels on its own, and a
# capture per attract-mode relabel would bury the real ones. Consequences are not here -- one
# arriving alone is recorded as unattributed rather than invented into a player action.
TRIGGERS = (
    "bet_button",           # a Line/Hold/Maxbet button on the deck
    "bet_config_changed",   # denomination or bet changed -- but only reasonForChange[Player]
    "touch",                # the touchscreen
    "bet_locked",           # a wager was placed, so a spin is away
    "spin_started",
    "hold_and_spin_started",
    "gamble_pick",          # red/black -- the only thing in a gamble round a person does
    "game_started",
)

# What ends a round *here*. Deliberately not `gamelog.TERMINAL`, which also holds `win`: spin.py
# must stop at the win because the press resolving it is one it will never make, while this is
# watching the person about to make it. So a round runs to `game_over`, which lands once the win has
# been collected or gambled, and the spin and its collect stay in one folder.
ROUND_OVER = ("game_over",)

# Points where the game announces something and then goes quiet for a long time, mapped to the events
# that end the wait. An action sitting on one is not idle and must not be closed on the ordinary
# window. Split by *who* is being waited for, because getting it wrong shows up as a round cut in half:
#
# PLAYER_WAITS -- the game will wait for a person for as long as it takes, with no timeout of its own,
# and there is no bound to guess at: a respin prompt sat 51.8 s, an uncollected win 3.2 hours. Any
# bound splits one spin into two folders, with the collect filed away from the spin that won it. So
# the round is held open (`watch.PLAYER_WAIT_S` 0) and the player's next input moves it on.
#
# GAME_WAITS -- the game is presenting and will get on with it, so a bound is meaningful. The bonus
# intro measured 27.7 s and 68.4 s, with the log silent for 29 s of it; without this the spin that
# triggered the bonus is filed as finished before the bonus starts.
PLAYER_WAITS = {"hold_and_spin_prompt": ("hold_and_spin_started",),
                "win": ("take_win", "gamble_played", "game_over"),
                "gamble_played": ("gamble_pick", "game_over"),
                "gamble_result": ("gamble_pick", "win", "gamble_over", "game_over")}
GAME_WAITS = {"bonus_triggered": ("feature_triggered",)}
LONG_WAITS = {**PLAYER_WAITS, **GAME_WAITS}

# Moments inside an action worth their own frame.
MILESTONES = (
    "reels_stopped",        # once per free spin during a feature, which is the point
    "mystery_reveal",
    "feature_triggered",
    "hold_and_spin_prompt",
    "hold_and_spin_started",
    "bonus_triggered",
    "jackpot_awarded",
    "jackpot_celebration",
    "wager_saver_offered",
    "win",
    "take_win",
    "gamble_played",
    "gamble_pick",
    "gamble_result",
    "denom_changed",
)

# Housekeeping the game logs constantly. Recorded in the timeline like everything else, but kept
# off the console -- an hour of watching would otherwise scroll past thousands of deck relabels.
# run.log gets them regardless, since it is always written at DEBUG.
ROUTINE = ("deck_changed", "idle_state", "gamble_state", "new_game_allowed", "cash_symbol",
           "bet_changed", "denom_changed", "bet_config_changed")

# Reading a transition back to the input that caused it. The game names the message; these are
# the ones a human is behind.
_INPUT_MESSAGES = {
    "GDK.Common.ServerAPI.SpinButtonMsg": "the spin button",
    "GDK.Common.ServerAPI.BetValueButtonMsg": "a bet button",
    "GDK.Common.ServerAPI.MaxBetButtonMsg": "the max bet button",
    "GDK.Common.ServerAPI.DenomSubThemeChangeButtonMsg": "the denomination button",
    "double_up_offer_decline": "the collect choice",
    "double_up_offer_accept": "the gamble choice",
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def record(event: gamelog.Event) -> dict:
    """One game-log event as a JSON-serialisable row, with the game's own clock."""
    row = {"event": event.name,
           "game_clock": event.at.strftime("%H:%M:%S.%f")[:-3] if event.at else None,
           "note": gamelog.describe(event)}
    row.update(event.fields)
    return row


def trigger_of(event: gamelog.Event) -> str | None:
    """Why this event means a person did something, or None if it doesn't.

    The string is for the console and the record; the caller only tests truthiness.
    """
    name, fields = event.name, event.fields
    if name == "bet_config_changed":
        # The cabinet changes its own denomination in attract mode. Only a player counts.
        if fields.get("reason") != "Player":
            return None
        flags = fields.get("flags", "")
        return ("denomination change" if "DenomChanged" in flags
                else "bet change" if "BetChanged" in flags else "bet configuration change")
    if name == "bet_button":
        return "a bet button on the deck"
    if name == "touch":
        return "the touchscreen"
    if name in ("bet_locked", "spin_started"):
        return "a spin began"
    if name == "hold_and_spin_started":
        return "Hold & Spin was started"
    if name == "gamble_pick":
        return f"a gamble pick ({str(fields.get('pick', '')).lower()})"
    if name == "game_started":
        return "the game client started"
    if name not in TRIGGERS:
        return None
    return name


def is_milestone(event: gamelog.Event) -> bool:
    """Whether this moment deserves a frame of its own.

    Not just membership of MILESTONES: `denom_changed` is logged on every single spin with
    "Did denom Change[False]", so taking it at face value spends a frame per spin photographing
    a denomination that did not change.
    """
    if event.name == "denom_changed":
        return event.fields.get("changed") == "True"
    return event.name in MILESTONES


def caused_by(events: list[dict]) -> str | None:
    """The input the game itself blamed a transition on, if it named one.

    The deck's log says *which button* was pressed but not what it did; the game log says what
    happened but not what was touched. This is the one place the game closes that gap, and it is
    the only way an action taken on the glass can be attributed at all.
    """
    for row in events:
        via = row.get("via")
        if via in _INPUT_MESSAGES:
            return _INPUT_MESSAGES[via]
    return None


def slug(kinds: list[str], limit: int = 60) -> str:
    """A folder-name fragment from a round's kinds: ["spin", "win"] -> "spin+win".

    Capped, because a round can be six things at once and the folder holds files with their own
    names underneath -- Windows still has a path length to stay inside.
    """
    parts = [_SLUG_RE.sub("-", kind.lower()).strip("-") for kind in kinds if kind]
    out = ""
    for part in parts:
        if out and len(out) + len(part) + 1 > limit:
            return out + "+more"
        out = f"{out}+{part}" if out else part
    return out or "activity"


class Action:
    """One thing the player started, and everything observed until the game finished it.

    For a spin that is the whole round, not just the part before the win: the game draws the boundary
    itself by not logging `game_over` until the win has been answered. So a round runs for as long as
    the player leaves it, and stays one folder however long they take -- see `waiting_for`.
    """

    def __init__(self, index: int, trigger: str, opened: datetime, deck: dict | None = None,
                 previous: dict | None = None):
        self.index = index
        self.trigger = trigger
        self.opened = opened
        self.deck_before = deck
        # What the bet and denomination were before this action. The log reports a "configuration
        # change" on every deck bet-button press, re-bets of the same amount included, so comparing
        # the numbers is the only way to tell a real change from a repeat.
        self.previous = previous or {}
        self.folder = ""
        self.buttons: list[dict] = []      # i-Deck presses attributed to this action
        self.events: list[dict] = []
        self.shots: list[dict] = []
        self.terminal: str | None = None
        self.ended_by = ""                 # why the follow stopped: terminal, quiet, ceiling
        self.measured_s = 0.0
        self.opened_mono = 0.0             # to measure from, when the player started the round
        self.milestone_n = 0

    # -- while it runs -----------------------------------------------------

    def add(self, event: gamelog.Event) -> dict:
        row = record(event)
        self.events.append(row)
        if event.name in ROUND_OVER and self.terminal is None:
            self.terminal = event.name
        return row

    @property
    def names(self) -> set[str]:
        return {row["event"] for row in self.events}

    # Everything that means the game took over and will report back in its own time. A gamble belongs
    # here as much as a spin: its game over lands 2.2 s after the round ends, and a collect's win
    # meter counts up for 3.5-7.9 s before one.
    IN_PLAY = {"bet_locked", "spin_started", "reels_spinning", "feature_triggered",
               "hold_and_spin_started", "gamble_played", "gamble_pick", "take_win",
               "bonus_triggered"}

    # A change to the wager, logged in full the moment it happens, with no outcome to wait for.
    SELF_CONTAINED = {"bet_config_changed", "bet_changed", "denom_changed"}

    @property
    def settles_fast(self) -> bool:
        """Whether this action is complete as soon as it stops logging.

        Only a wager change qualifies: "nothing more has been logged" is not "nothing more is
        coming", and a touch that started a free spin round was followed by 13 s of silence before
        the reels moved.
        """
        return bool(self.names & self.SELF_CONTAINED) and not (self.names & self.IN_PLAY)

    @property
    def waiting_for(self) -> str | None:
        """Who the game is waiting for -- `"player"`, `"game"`, or None if it isn't waiting.

        "More to come" and "quiet for good" look identical from a stopped log and want opposite
        treatment. Two things here are load-bearing, and each was a bug:

        Read as a **state** (announcement seen, resolution not yet) rather than "the last event was
        an announcement" -- the game logs a relabel and some transitions after putting a prompt up,
        so the prompt is never the last line.

        Walked **forwards**, each announcement matched only against *its own* resolutions. Scanning
        backwards for "a resolution of anything" never recognised a waiting win, `win` being both an
        announcement and a resolution of a gamble result, which closed 25 winning rounds mid-round
        over 525 replayed rounds.
        """
        if self.terminal:
            return None
        pending = None
        for row in self.events:
            name = row["event"]
            # Resolve first, then re-arm: `gamble_played` answers a win and starts a wait of its
            # own for the card to be picked, and both have to happen.
            if pending and name in LONG_WAITS[pending]:
                pending = None
            if name in LONG_WAITS:
                pending = name
        if pending is None:
            return None
        return "player" if pending in PLAYER_WAITS else "game"

    def moved_on(self, event: gamelog.Event) -> bool:
        """Whether this event means the game has left this round behind.

        Unbounded must not mean "forever, even if the answer never gets logged". Two events say it is
        not coming: `game_started` (the client restarted) and a second `bet_locked` (a fresh wager, so
        a new game -- free spins do not stake, and no round in 538 replayed logged it twice). Without
        this, one abandoned win swallowed 11 hours of play into a single folder.
        """
        if not self.events:
            return False        # the event that opened the round is not a boundary in it
        if event.name == "game_started":
            return True
        return event.name == "bet_locked" and "bet_locked" in self.names

    @property
    def awaiting_collect(self) -> bool:
        """Whether this round is sitting on a win nobody has answered yet.

        Genuinely unfinished: the game logs no game over until the win is collected or gambled, so the
        round stays open and the collect lands in the folder of the spin it belongs to.
        """
        if self.terminal:
            return False
        for row in reversed(self.events):
            if row["event"] in ("take_win", "gamble_played"):
                return False
            if row["event"] == "win":
                return True
        return False

    # -- what it was -------------------------------------------------------

    def rows(self, name: str) -> list[dict]:
        return [row for row in self.events if row["event"] == name]

    @property
    def kinds(self) -> list[str]:
        """Everything this round was, in the order it happened.

        Not one label -- a round is routinely several things at once, and picking one throws the rest
        away. Chronological, so the folder name reads as the round: `007_spin+jackpot+win+collect`.
        """
        names = self.names
        bet, denom = self.bet
        # reasonForChange[Attract] is the cabinet playing with itself, and never a player action.
        flags = " ".join(row.get("flags", "") for row in self.rows("bet_config_changed")
                         if row.get("reason") == "Player")
        spun = bool(names & {"bet_locked", "spin_started", "reels_spinning"})

        kinds = []
        if "game_started" in names:
            kinds.append("game restart")
        if any(row.get("changed") == "True" for row in self.rows("denom_changed")) \
                or "DenomChanged" in flags \
                or (denom and self.previous.get("denom") not in (None, denom)):
            kinds.append("denomination change")
        if (bet and self.previous.get("bet") not in (None, bet)) \
                or ("BetChanged" in flags and not spun):
            # The second case is a wager change whose new total never reached the log -- rare,
            # but "something about the wager changed" beats reporting nothing.
            kinds.append("bet change")
        if spun:
            kinds.append("spin")
        if names & {"hold_and_spin_started", "hold_and_spin_prompt"}:
            kinds.append("hold and spin")
        for row in self.rows("feature_triggered"):
            if row.get("feature"):
                kinds.append(f"free spins ({row['feature']})")
        if names & {"jackpot_awarded", "jackpot_celebration"}:
            kinds.append("jackpot")
        if "wager_saver_offered" in names:
            kinds.append("wager saver")
        if "win" in names:
            kinds.append("win")
        if names & {"gamble_played", "gamble_pick"}:
            kinds.append("gamble")
        if "take_win" in names:
            kinds.append("collect")
        if not kinds:
            # A press the game did nothing with -- Service, or a button that isn't live in this
            # state. Worth recording: "nothing happened" is the answer to a real question.
            kinds.append("no game response" if not self.events else "activity")
        return list(dict.fromkeys(kinds))

    @property
    def bet(self) -> tuple[str | None, str | None]:
        """The bet and denomination this action ended on, if either was logged."""
        bet = denom = None
        for row in self.events:
            bet = row.get("total_bet") or bet
            denom = row.get("denom") or denom
        return bet, denom

    def summary(self) -> dict:
        kinds = self.kinds
        bet, denom = self.bet
        return {
            "action": ", ".join(kinds),
            "kinds": kinds,
            "slug": slug(kinds),
            "caused_by": caused_by(self.events),
            "bet": bet,
            "denom": denom,
        }

    def to_json(self) -> dict:
        return {
            "index": self.index,
            "folder": self.folder,
            "opened_at": self.opened.isoformat(timespec="milliseconds"),
            "trigger": self.trigger,
            "buttons": self.buttons,
            "previous": self.previous,
            **self.summary(),
            "measured_s": round(self.measured_s, 3),
            "terminal_event": self.terminal,
            "ended_by": self.ended_by,
            "awaiting_collect": self.awaiting_collect,
            "deck_before": self.deck_before,
            "shots": self.shots,
            "events": self.events,
        }
