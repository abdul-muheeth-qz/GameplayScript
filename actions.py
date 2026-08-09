"""What the player just did, worked out from the two logs.

spin.py knows what a spin is because it caused one: it marks the log, presses a button, and
everything after the mark belongs to that press. Nothing here has that luxury. The player is at
the cabinet pressing whatever they like, and this module's whole job is to recognise the
beginning of an action, the end of it, and what it was -- from the log alone.

Four questions, answered separately:

  * **Did an action just start?** `trigger_of` -- a small set of markers that only ever appear
    because a human did something. An i-Deck press is the other trigger and is handled by the
    caller, because it comes from the panel service's log rather than the game's.
  * **Is this moment worth a frame?** `MILESTONES` -- the points inside an action where the
    screen changes to something worth having a picture of. A Hold & Spin runs ~50 s over 20-odd
    free spins, so one shot at each end would miss the whole feature.
  * **Is it over, or is the game just waiting?** `Action.waiting_for` -- and if it is waiting,
    whether for the *player* (unbounded: the game has no timeout of its own and neither do we) or
    for its own presentation to finish (bounded). A round is never closed and reopened; while the
    game waits for the person, so does the round.
  * **What was it?** `Action.summary` -- a list of kinds rather than one label, because one
    press is routinely two things: with a win pending, Repeat Bet reads "Collect Win" and means
    "collect, then bet again", so that press is a collect *and* a spin.

Nothing in here does any I/O or touches the clock beyond stamping records, so it is the one
part of this tool that can be reasoned about without a cabinet in front of you.

Two markers are worth knowing about because they are what make manual actions legible at all:

  * `[Game.BetConfigurationChanged] betChangedFlags[...] reasonForChange[Player]` says the bet or
    the denomination changed *and* that a person did it. The cabinet cycles denominations by
    itself in attract mode (reasonForChange[Attract], 8 occurrences in one log against 41
    Player ones), and those must not be captured as player actions.
  * `TouchMsg` is the only record anywhere that the screen was touched. Collect, gamble and
    "touch to start" can all be done on the glass instead of the deck, and without this marker
    they are invisible until their consequence lands.
"""

from __future__ import annotations

import re
from datetime import datetime

import gamelog

# Events that mean a person did something. Reaching one of these while nothing is being followed
# opens an action. Deliberately not "any event at all": the game logs state transitions and deck
# relabels on its own, and a capture per attract-mode relabel would bury the real ones.
#
# Consequences are not in here. `win`, `feature_triggered` and the rest arrive *inside* an action
# that is already open; if one somehow arrives on its own it is recorded as unattributed rather
# than invented into a player action.
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

# What ends a round *here*. Deliberately not `gamelog.TERMINAL`, which spin.py needs and which
# also contains `win`: spin.py has to stop at the win because the press that resolves it is one
# it will never make, while this tool is watching the person who is about to make it. So a round
# runs to the game's own `game_over`, which lands only once the win has been collected or
# gambled -- and everything between the two stays in one folder.
ROUND_OVER = ("game_over",)

# Points where the game has announced that something is coming and then goes quiet for a long
# time, mapped to the events that end the wait. An action sitting on one of these is not idle and
# must not be closed on the ordinary window.
#
# Split by *who* the game is waiting for, because the two want different treatment and getting it
# wrong shows up as a round cut in half:
#
# PLAYER_WAITS -- the game has put something up and will wait for a person for as long as it
# takes, with no timeout of its own. There is no upper bound to guess at: one player left a
# respin prompt 51.8 s, another left a win uncollected 3.2 hours, and one first gamble pick took
# 36 s. Whatever bound is chosen, a longer pause exists -- and closing the round on it splits one
# spin into two folders, with the collect filed away from the spin that won it. So the round is
# held open instead (`watch.player_wait_s`, 0 = as long as it takes), and the *player's next
# input* is what moves it on.
#
# GAME_WAITS -- the game is presenting something and will get on with it by itself, so a bound is
# meaningful. `bonus_triggered` is the bonus intro: measured at 27.7 s and 68.4 s between the
# announcement and the feature actually starting, with the raw log silent for 29 s of it. Without
# this the spin that triggered the bonus -- the one worth capturing most -- is filed as finished
# before the bonus starts.
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

    For a spin that is the whole round -- the bet, the reels, any feature, the win offer, the
    gamble or the collect, and the game over -- not just the part before the win. The game
    itself draws that boundary: it does not log `game_over` until the win has been answered, so
    following it to `game_over` keeps one round in one folder.

    A round can therefore run for as long as the player leaves it: the game waits forever on an
    uncollected win, and so does this. One round is one folder with one `after` frame, however
    long the person took to answer -- see `waiting_for`.
    """

    def __init__(self, index: int, trigger: str, opened: datetime, deck: dict | None = None,
                 previous: dict | None = None):
        self.index = index
        self.trigger = trigger
        self.opened = opened
        self.deck_before = deck
        # What the bet and denomination were before this action. The log says a bet
        # "configuration change" happened on every deck bet-button press, including the ones
        # that re-bet the same amount, so the only way to tell a real change from a repeat is
        # to compare the numbers.
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

    # Everything that means the game took over and will report back in its own time: a spin, a
    # feature, a gamble round, a collect. A gamble belongs here as much as a spin does -- the
    # game logs its game over 2.2 s after the round ends, and an action closed before that loses
    # the outcome of the spin it belonged to. So does a collect: the win meter counts up for
    # 3.5-7.9 s (measured) before the game over.
    IN_PLAY = {"bet_locked", "spin_started", "reels_spinning", "feature_triggered",
               "hold_and_spin_started", "gamble_played", "gamble_pick", "take_win",
               "bonus_triggered"}

    # A change to the wager, logged in full the moment it happens, with no outcome to wait for.
    SELF_CONTAINED = {"bet_config_changed", "bet_changed", "denom_changed"}

    @property
    def settles_fast(self) -> bool:
        """Whether this action is complete as soon as it stops logging.

        Only a wager change qualifies. Everything else -- including a bare touch or a press the
        game hasn't answered yet -- gets the full gameplay window, because "nothing more has
        been logged" is not the same as "nothing more is coming": a touch that starts a free
        spin round was followed by 13 s of silence before the reels moved, and closing it early
        orphaned the whole round.
        """
        return bool(self.names & self.SELF_CONTAINED) and not (self.names & self.IN_PLAY)

    @property
    def waiting_for(self) -> str | None:
        """Who the game is waiting for -- `"player"`, `"game"`, or None if it isn't waiting.

        "The game has more to come" and "the game has gone quiet for good" look identical from a
        log that has stopped moving, and they want opposite treatment: one is the middle of an
        action, the other is the end of one. And within the first, waiting for a person is
        unbounded while waiting for a presentation is not.

        Read as a state -- announcement seen, resolution not yet -- rather than as "the last event
        was an announcement". The game logs a deck relabel and a couple of state transitions after
        putting a prompt up, so the prompt is never the last line, and testing for that missed
        every one of them.

        Walked forwards, and each announcement matched only against *its own* resolutions. An
        earlier version scanned backwards for "a resolution of anything" and so never once
        recognised a waiting win: `win` is both an announcement and one of the events that
        resolves a gamble result, so the win resolved itself the moment it was logged and the
        round fell back to the ordinary window. Replayed over both of this cabinet's logs -- 525
        rounds, 10 hours of play -- that closed 25 winning rounds mid-round, before the collect
        they were waiting for.
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

        A round is held open for as long as the game waits for the player, which is unbounded on
        purpose -- but unbounded must not mean "forever, even if the answer never gets logged".
        Two events say the answer is not coming:

          * `game_started` -- the client restarted. Whatever the round was waiting for died with
            the old process.
          * a second `bet_locked` -- a fresh wager was staked, so this is a new game. Free spins
            and respins do not stake, and across 538 replayed rounds none logged `bet_locked`
            twice, so a second one is never part of the same round.

        Without this, a win left standing when the cabinet restarted swallowed the next 11 hours
        of play into one folder -- measured, replaying real logs.
        """
        if not self.events:
            return False        # the event that opened the round is not a boundary in it
        if event.name == "game_started":
            return True
        return event.name == "bet_locked" and "bet_locked" in self.names

    @property
    def awaiting_collect(self) -> bool:
        """Whether this round is sitting on a win that nobody has answered yet.

        The round is genuinely unfinished: the game will not log its game over until the win is
        collected or gambled, and it will wait forever for that. So the round stays open, and the
        collect -- and the game over that follows it -- land in the folder of the spin they
        belong to.
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

        Not one label: a round is routinely several things at once -- a spin that triggered a
        bonus, awarded a jackpot, offered the win and had it gambled is all of those, and
        picking one would throw the rest away. Chronological order so the folder name reads as
        the story of the round: `007_spin+hold-and-spin+jackpot+win+collect`.
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
