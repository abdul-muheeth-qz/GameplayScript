#!/usr/bin/env python
"""Watch someone play, and capture a folder per round.

    python -m capture.watch              # watch until Ctrl-C
    python -m capture.watch --dry-run    # check everything, shoot one frame, press nothing, exit
    python -m capture.watch --duration 900 --max-rounds 40

This presses nothing. A person plays the cabinet by hand -- spins, changes the denomination,
collects, gambles, starts a Hold & Spin, sits through a bonus -- and this notices each of those
from the logs, screenshots the screen while it happens, and writes down what the game said. It
is the passive half of spin.py: same OBS, same windows, same two logs, no click.

**One folder is one whole round**, from the bet to the game over: the spin, the reels, any
feature, the win offer, and the gamble or collect that answers it. The game itself draws that
boundary -- it does not log `GameOverMsg` until a win has been collected or gambled -- so
following it to `game_over` keeps a round together instead of filing the collect separately.
(`gamelog.TERMINAL` stops at the win because spin.py has to: the press that resolves it is one
spin.py will never make. This tool is watching the person who is about to make it.) Things that
are not part of a round -- a denomination change, a bet change -- get a folder of their own.

    captures/watch_2026-08-06_170314/
      run.log
      session.json                    rewritten after every round, so an interruption costs nothing
      rounds/
        003_spin+win+collect/
          before.png                  the standing frame from just before the press
          trigger.png                 taken the moment the round was noticed
          m01_reels_stopped.png       one per milestone -- a Hold & Spin has twenty-odd
          m03_take_win.png
          after.png                   once the game says it is finished
          round.json

Recognising a round is the whole problem here. spin.py knows what a spin is because it caused
one; this has to read it off the log (see actions.py). Two sources, because neither is enough
alone: the panel service's log says *which button* was pressed but not what it did, and the
game's log says what happened but not what was touched -- and a collect made on the touchscreen
never reaches the panel log at all.

Knowing when a round is *over* is the same problem spin.py has, solved the same way: the game
log is asked, never a fixed delay. An ordinary spin is ~3.3 s and a Hold & Spin ran 53 s. Three
differences, all measured by replaying real logs (see the settings in Watch.__init__):

  * A bet or denomination change produces no game over and is complete the moment it is logged,
    so it closes on a two-second window rather than the gameplay one. Nothing else qualifies --
    an unanswered touch had 13 s of silence before the reels moved.
  * The gameplay window itself is much longer than spin.py's, because a watcher has no press to
    anchor to and the silences inside a feature run to 29 s.
  * When the game is waiting for the *person* -- a win on the collect/gamble offer, a Hold & Spin
    respin prompt, a gamble waiting for a card -- it will wait forever, and so does the round.
    One sat 3.2 hours. There is no bound to guess at and no clock running: the round stays open,
    in one folder with one `after` frame, until the player answers it (`watch.player_wait_s`,
    0 by default, meaning as long as it takes).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime

from ..settings import resolve

from . import actions, gamelog, ideck, spin, winfocus
from .obs_client import ObsError

LOG = logging.getLogger("watch")

# How long a capture failure is left alone before the game window is looked up again. A
# denomination change reloads the game's scene and the client occasionally restarts outright,
# either of which can leave OBS pointed at a window that no longer exists.
REACQUIRE_EVERY_S = 15.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m capture.watch", description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="check everything and save one screenshot, then exit")
    parser.add_argument("--duration", type=float, metavar="SECONDS",
                        help="stop after this long (default: run until Ctrl-C)")
    parser.add_argument("--max-rounds", type=int, metavar="N",
                        help="stop after capturing N rounds")
    parser.add_argument("--no-milestones", action="store_true",
                        help="one frame at each end of a round instead of one per milestone")
    parser.add_argument("--out", help="base folder that run folders are created in")
    parser.add_argument("--config", default=None,
                        help="path to config.json (default: server/config.json)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="debug logging (always written to run.log regardless)")
    return parser


# -- the waits -------------------------------------------------------------
# `watch.py` has spin.py's rule -- no fixed delay anywhere -- and four windows instead of one,
# because here the log going quiet means different things. All four were tuned by replaying 11
# hours of real play (485 rounds) through `actions.py`, so they are measurements and live beside
# the loop rather than in config.json; re-run that replay after changing any of them.

# The gameplay wait. It restarts on every event, so a feature is followed for as long as it keeps
# talking. 35 s is not a typo and is nearly free: 93% of actions close on the game's own terminal
# event, so this is a fallback, and the silences inside a feature run to 29 s. At spin.py's 8 s,
# 49 spin outcomes fell outside every action; at 35 s, two did.
IDLE_TIMEOUT_S = 35.0
# For an action that is complete as soon as it stops logging -- a wager change, and nothing else.
QUIET_S = 2.0
# How long the game may be quiet after announcing something it will get on with by itself: a
# bonus intro playing, measured at 68.4 s.
LONG_WAIT_S = 90.0
# How long it may wait for the *person*: a win on the offer, a Hold & Spin respin prompt, a gamble
# waiting for a card. 0 means "as long as it takes", which is what the game itself does -- it has
# no timeout there, so any bound is a guess, and a wrong guess splits one spin across two folders.
# `Action.moved_on` and `Watch.ceiling_from` are what stop unbounded becoming stuck.
PLAYER_WAIT_S = 0.0
# The settle before an after shot. Scheduled, never slept -- the press after a game over was
# measured at 0.44 s, inside this.
AFTER_DELAY_MS = 800
TAIL_QUIET_S = 1.0
# The backstop on one action. It must not count time parked on an offer -- see Watch.ceiling_from.
ACTION_TIMEOUT_S = 300.0
POLL_INTERVAL_MS = 50
# The standing before-frame. 0 turns it off.
PREROLL_S = 1.0
MILESTONE_SHOTS = True
MILESTONE_MIN_GAP_MS = 400


# -- the watch -------------------------------------------------------------


class Watch:
    """The session: two logs polled in one loop, and the frames that come out of it.

    Everything is time-sliced rather than waited on. The obvious shape -- notice a press, sleep
    for the settle delay, shoot -- would go deaf for the length of the sleep, and a player does
    not wait for us: the collect and the re-bet after a win can be half a second apart. So the
    after shot is *scheduled* and the loop keeps polling. Nothing is lost by that; both logs are
    read by byte offset, so events that arrive while we are busy are still there next tick.
    """

    def __init__(self, obs, cfg: dict, run_dir: str, game, panel, watcher, press_log,
                 size, native, state: dict, milestones: bool):
        self.obs = obs
        self.run_dir = run_dir
        self.rounds_dir = os.path.join(run_dir, "rounds")
        self.game = game
        self.panel = panel
        self.watcher = watcher
        self.press_log = press_log
        self.size = size
        self.native = native       # what the source itself has, before capture.scale

        capture = cfg.get("capture", {})
        self.capture_cfg = capture
        self.scene = capture.get("scene", "Scene")
        self.source = capture.get("source", "Window Capture")
        self.format = capture.get("format", "png")
        self.quality = int(capture.get("quality", -1))
        self.game_cfg = cfg["game"]

        # The four waits, and the rest of the loop's timing. All module constants -- see "the
        # waits" above for what each one was measured against.
        self.idle_timeout = IDLE_TIMEOUT_S
        self.quiet_s = QUIET_S
        self.long_wait_s = LONG_WAIT_S
        self.player_wait_s = PLAYER_WAIT_S
        self.after_delay = AFTER_DELAY_MS / 1000
        self.tail_quiet = TAIL_QUIET_S
        self.ceiling = ACTION_TIMEOUT_S
        self.interval = POLL_INTERVAL_MS / 1000.0
        self.preroll_s = PREROLL_S
        self.milestone_gap = MILESTONE_MIN_GAP_MS / 1000.0
        self.milestones = milestones and MILESTONE_SHOTS

        self.by_position = {b.position: b.name for b in panel.buttons} if panel else {}
        # Seeded from the log's history so the first action can be judged against something: a
        # bet "change" that re-bets the same amount is not a change.
        self.wager = {"bet": state.get("bet"), "denom": state.get("denom")}

        self.action: actions.Action | None = None
        # When the ceiling starts counting from. Not the same as when the round opened: time the
        # round spends parked waiting for the player is not time it is running (see settle).
        self.ceiling_from = 0.0
        self.last_event = 0.0
        self.terminal_at: float | None = None
        self.after_done = False
        self.closing = False       # the after shot is taken; trailing events still welcome
        self.last_milestone = 0.0
        self.held_note = 0.0       # last time a held-open round was mentioned on the console

        self.done: list[dict] = []
        self.header: dict = {}     # set once the run knows it; session.json needs it
        self.unattributed: dict[str, int] = {}
        self.capture_errors = 0
        self.last_reacquire = 0.0

        self.preroll_dir = os.path.join(run_dir, ".preroll")
        self._preroll_file = os.path.join(self.preroll_dir, f"frame.{self.format}")
        self._preroll_at: datetime | None = None
        self._next_preroll = 0.0

    @property
    def finished(self) -> bool:
        """Whether the game has already declared the open action over.

        From here on the next thing the player does is a new action, whether or not the after
        shot has been taken yet.
        """
        return self.closing or self.terminal_at is not None

    # -- frames ------------------------------------------------------------

    def shoot(self, path: str, kind: str) -> dict | None:
        """One frame, or None if OBS refused it.

        A refusal must not end the session. The game window can go away mid-watch -- a
        denomination change reloads the scene -- and the right answer is to note the gap, find
        the window again and carry on, not to lose the next twenty minutes of play.
        """
        try:
            entry = spin.shot(self.obs, self.source, path, self.size, self.format, self.quality)
        except (ObsError, OSError) as exc:
            self.capture_errors += 1
            LOG.warning("WARNING: could not capture %s: %s", kind, exc)
            self.reacquire("a capture failed")
            return None
        entry["kind"] = kind
        return entry

    def preroll(self, now: float) -> None:
        """Keep a recent frame of the idle screen, so an action has a genuine 'before'.

        An action is only noticed once it has started, so the first frame it can take is already
        a fraction of a second late -- long enough for the reels to be moving. This keeps one
        standing frame while nothing is happening; when an action opens, that frame becomes its
        before shot, with its age recorded rather than glossed over.
        """
        if self.preroll_s <= 0 or self.action is not None or now < self._next_preroll:
            return
        self._next_preroll = now + self.preroll_s
        os.makedirs(self.preroll_dir, exist_ok=True)
        try:
            spin.shot(self.obs, self.source, self._preroll_file, self.size, self.format,
                      self.quality)
            self._preroll_at = datetime.now()
        except (ObsError, OSError) as exc:
            # Quietly: this fires every second, and a genuine problem will surface on the next
            # real shot with a warning attached.
            LOG.debug("preroll frame failed: %s", exc)
            self._preroll_at = None

    def take_preroll(self, folder: str) -> dict | None:
        if not self._preroll_at or not os.path.isfile(self._preroll_file):
            return None
        target = os.path.join(folder, f"before.{self.format}")
        try:
            shutil.copy2(self._preroll_file, target)
        except OSError as exc:
            LOG.debug("could not keep the preroll frame: %s", exc)
            return None
        age = (datetime.now() - self._preroll_at).total_seconds()
        return {"kind": "before", "file": os.path.basename(target),
                "bytes": os.path.getsize(target),
                "wall_clock": self._preroll_at.isoformat(timespec="milliseconds"),
                "age_ms": int(age * 1000)}

    def reacquire(self, why: str) -> None:
        """Find the game window and re-measure the capture, after something moved under us."""
        now = time.monotonic()
        if now - self.last_reacquire < REACQUIRE_EVERY_S:
            return
        self.last_reacquire = now
        LOG.info("looking the game window up again (%s)", why)
        try:
            self.game = winfocus.find_window(self.game_cfg["process"],
                                             self.game_cfg.get("window_class", "UnityWndClass"))
            winfocus.ensure_restored(self.game)
            self.native = spin.capture_size(self.obs, self.scene, self.source, self.game,
                                            attempts=6, delay=0.5)
            self.size = spin.requested_size(self.native, self.capture_cfg)
        except (winfocus.WindowNotFound, ObsError, RuntimeError) as exc:
            LOG.warning("WARNING: %s -- capturing may be broken until the game is back", exc)

    # -- an action, start to finish ---------------------------------------

    def open(self, trigger: str, deck: dict | None = None) -> actions.Action:
        """Start a round.

        One round is one folder, from the trigger to the game over -- including a wait for the
        player in the middle of it, however long. Nothing is ever closed and reopened: while the
        game is parked on the collect/gamble offer it will take no other input, so the next thing
        the player does is by definition the thing that answers it, and it belongs to this round.
        """
        index = len(self.done) + 1
        action = actions.Action(index, trigger, datetime.now(), deck, dict(self.wager))
        action.opened_mono = time.monotonic()
        # Named for the trigger now and renamed to what it turned out to be on close, because
        # what it was is not knowable until the game has answered.
        action.folder = os.path.join(self.rounds_dir, f"{index:03d}")
        LOG.info("#%03d %s  %s", index, action.opened.strftime("%H:%M:%S"), trigger)
        os.makedirs(action.folder, exist_ok=True)

        self.action = action
        self.ceiling_from = self.last_event = time.monotonic()
        self.terminal_at = None
        self.after_done = self.closing = False
        self.last_milestone = self.held_note = 0.0

        before = self.take_preroll(action.folder)
        if before:
            action.shots.append(before)
        shot = self.shoot(os.path.join(action.folder, f"trigger.{self.format}"), "trigger")
        if shot:
            action.shots.append(shot)
        return action

    def shoot_after(self) -> None:
        """The last frame of the round. One per round: the round is not closed until the game has
        finished it, so there is never a second one to number."""
        action = self.action
        if action is None or self.after_done:
            return
        shot = self.shoot(os.path.join(action.folder, f"after.{self.format}"), "after")
        if shot:
            action.shots.append(shot)
        self.after_done = True

    def milestone(self, name: str) -> None:
        action = self.action
        now = time.monotonic()
        if not self.milestones or action is None or self.closing:
            return
        if now - self.last_milestone < self.milestone_gap:
            # A burst of events can land in one tick; a frame per event would be the same
            # picture several times over.
            return
        self.last_milestone = now
        action.milestone_n += 1
        shot = self.shoot(
            os.path.join(action.folder, f"m{action.milestone_n:02d}_{name}.{self.format}"),
            f"milestone:{name}")
        if shot:
            action.shots.append(shot)

    def cut_short(self, why: str) -> None:
        """Finish the open action now, because the player has started another one.

        Measured here: after the game logs its game over, the next press came 0.44 s later --
        sooner than the 0.8 s the after shot waits for the screen to settle. Without this, that
        press and everything it caused would be filed under the previous spin, and the spin
        before it would never get an after shot at all. So the moment the game has declared an
        action finished, the next thing the player does starts a new one, settle delay or not.
        """
        if self.action is None:
            return
        self.shoot_after()
        self.close(why)

    def close(self, ended_by: str) -> None:
        action = self.action
        if action is None:
            return
        self.action = None
        action.ended_by = ended_by
        action.measured_s = time.monotonic() - action.opened_mono

        bet, denom = action.bet
        self.wager = {"bet": bet or self.wager["bet"], "denom": denom or self.wager["denom"]}

        summary = action.to_json()
        if "spin" in action.kinds:
            # The same reading spin.py gives a spin, so the two tools describe one the same way.
            summary.update(spin.classify(action.events))
        folder = self.rename(action, summary)
        summary["folder"] = os.path.basename(folder)
        with open(os.path.join(folder, "round.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)

        self.done.append(summary)
        LOG.info("     %s -- %s in %.1fs, %d frame%s (%s)%s",
                 summary["action"],
                 summary.get("outcome", action.terminal or ended_by),
                 action.measured_s, len(action.shots), "" if len(action.shots) == 1 else "s",
                 ended_by,
                 " -- the win was never collected" if action.awaiting_collect else "")
        self._next_preroll = 0.0  # start the standing frame again straight away
        self.write()  # after every round, so nothing is lost if this is killed

    def rename(self, action: actions.Action, summary: dict) -> str:
        """Put what the round turned out to be in the folder name: 003 -> 003_spin+win+collect."""
        wanted = os.path.join(self.rounds_dir, f"{action.index:03d}_{summary['slug']}")
        if wanted == action.folder:
            return action.folder
        try:
            os.rename(action.folder, wanted)
        except OSError as exc:
            # Not worth failing over -- the name is a convenience and round.json says the same.
            LOG.debug("could not rename %s: %s", action.folder, exc)
            return action.folder
        action.folder = wanted
        return wanted

    # -- the loop ----------------------------------------------------------

    def on_press(self, position: int) -> None:
        """An i-Deck button was pressed -- by hand, since this tool presses nothing."""
        name = self.by_position.get(position, f"position {position}")
        entry = {"name": name, "position": position,
                 "at": datetime.now().isoformat(timespec="milliseconds")}
        if self.action is None or self.finished:
            self.cut_short("the player pressed something else")
            self.open(f"the {name} button on the deck")
        self.action.buttons.append(entry)
        LOG.debug("press: %s (position %d)", name, position)

    def on_event(self, event: gamelog.Event) -> None:
        trigger = actions.trigger_of(event)
        # A round waiting for the player is held open with no timeout, so something has to end it
        # when the answer never arrives: a client restart, or a new wager. See Action.moved_on.
        if self.action is not None and not self.finished and self.action.moved_on(event):
            self.cut_short("the game moved on without answering")
        if self.action is None:
            if not trigger:
                # Not a player action and nothing is open: the game talking to itself. Counted
                # rather than dropped, because a trigger this tool doesn't know about would show
                # up here first.
                self.unattributed[event.name] = self.unattributed.get(event.name, 0) + 1
                LOG.debug("unattributed: %s", gamelog.describe(event))
                return
            self.open(trigger)
        elif trigger and self.finished:
            self.cut_short("the player started something else")
            self.open(trigger)

        row = self.action.add(event)
        self.last_event = time.monotonic()
        line = LOG.debug if event.name in actions.ROUTINE else LOG.info
        line("     %s  %s", row["game_clock"] or "--:--:--", row["note"])

        if event.name == "game_started":
            self.reacquire("the game client restarted")
        if self.action.terminal and self.terminal_at is None:
            self.terminal_at = self.last_event
        elif actions.is_milestone(event):
            self.milestone(event.name)

    def settle(self, now: float) -> None:
        """Decide whether the open action is finished, and shoot its last frame when it is."""
        action = self.action
        if action is None:
            return

        if self.terminal_at is not None:
            # The game says the spin is over while its last frame is still being drawn, and on a
            # win the meter is still counting up. This is the one deliberate delay in the tool.
            if not self.after_done and now >= self.terminal_at + self.after_delay:
                self.shoot_after()
                self.closing = True
            elif self.after_done and now - self.last_event >= self.tail_quiet:
                # The game logs a spin's final grid a few milliseconds *after* its game over, so
                # closing on the terminal event itself would cut the record short.
                self.close("the game finished it")
            return

        waiting = action.waiting_for
        quiet = (self.player_wait_s if waiting == "player"
                 else self.long_wait_s if waiting == "game"
                 else self.quiet_s if action.settles_fast else self.idle_timeout)
        if quiet <= 0:
            # The game is waiting for the person and has no timeout of its own, so neither does
            # the round: it stays open until they answer it. The ceiling is pushed along with it,
            # because it asks whether the round has been *running* too long and a round parked on
            # an offer is not running -- otherwise a round held for an hour is over the ceiling
            # the moment the player answers, and closes before the game logs the game over.
            # Said out loud now and then, because a held round otherwise looks like a hung tool.
            self.ceiling_from = now
            self.held(now, action)
            return
        if now - self.last_event >= quiet:
            self.shoot_after()
            self.close("it went quiet")
        elif now - self.ceiling_from >= self.ceiling:
            LOG.warning("WARNING: action #%03d was still going after %.0fs "
                        "(watch.action_timeout_s); shooting anyway", action.index, self.ceiling)
            self.shoot_after()
            self.close("the ceiling expired")

    def held(self, now: float, action: actions.Action, every: float = 60.0) -> None:
        """Mention, once a minute, that a round is open only because the game is waiting."""
        if self.held_note and now - self.held_note < every:
            return
        if not self.held_note:  # nothing to report until it has actually been a while
            self.held_note = now
            return
        self.held_note = now
        LOG.info("     #%03d is still open: the game is waiting for the player (%.0fs so far)",
                 action.index, now - self.last_event)

    def tick(self) -> None:
        for position in self.press_log.poll() if self.press_log else ():
            self.on_press(position)
        for event in self.watcher.poll():
            self.on_event(event)
        self.settle(time.monotonic())
        self.preroll(time.monotonic())

    def run(self, duration: float | None, max_rounds: int | None) -> str:
        """Poll until told to stop. Returns why it stopped."""
        deadline = time.monotonic() + duration if duration else None
        while True:
            self.tick()
            # Both limits wait for the open action to finish rather than cutting it in half --
            # except when it is only open because the game is waiting for the player and that wait
            # is unbounded, which would mean never reaching the limit at all. finish() closes the
            # round on the way out, exactly as Ctrl-C does.
            held = self.player_wait_s <= 0 and self.action is not None \
                and self.action.waiting_for == "player"
            if self.action is None or held:
                counted = len(self.done) + (0 if self.action is None else 1)
                if max_rounds and counted >= max_rounds:
                    return f"{max_rounds} rounds captured"
                if deadline and time.monotonic() >= deadline:
                    return f"the {duration:.0f}s duration expired"
            time.sleep(self.interval)

    def finish(self) -> None:
        """Close whatever is open, so an interrupted round is still written out."""
        if self.action is None:
            return
        self.shoot_after()
        self.close("the watch stopped")

    # -- the record --------------------------------------------------------

    def counts(self) -> dict:
        tally: dict[str, int] = {}
        for record in self.done:
            for kind in record["kinds"]:
                tally[kind] = tally.get(kind, 0) + 1
        return dict(sorted(tally.items(), key=lambda kv: -kv[1]))

    def write(self, stopped_by: str = "") -> None:
        """Rewrite session.json. Called after every round, so an interruption costs nothing."""
        if not self.header:
            return  # the run hasn't started yet; there is nothing to describe
        record = dict(self.header)
        record.update({
            "rounds": self.done,
            "round_count": len(self.done),
            "counts": self.counts(),
            "unattributed_events": self.unattributed,
            "capture_errors": self.capture_errors,
            "stopped_by": stopped_by,
            "ended_at": datetime.now().isoformat(timespec="milliseconds"),
        })
        path = os.path.join(self.run_dir, "session.json")
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
        os.replace(temp, path)  # so a session.json is never half-written


# -- the run ---------------------------------------------------------------


def run(args) -> int:
    try:
        cfg = spin.load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read config {args.config or spin.DEFAULT_CONFIG}: {exc}",
              file=sys.stderr)
        return spin.EXIT_ERROR

    obs_cfg = cfg.get("obs", {})
    capture_cfg = cfg.get("capture", {})
    game_cfg = cfg["game"]
    ideck_cfg = cfg.get("ideck", {})

    scene = capture_cfg.get("scene", "Scene")
    source = capture_cfg.get("source", "Window Capture")
    img_format = capture_cfg.get("format", "png")
    # `resolve` anchors a relative --out on `server/`, where the config files and
    # captured_files/ live, rather than on wherever this was started from.
    base_out = resolve(args.out or cfg.get("output", {}).get("dir", "captures"))

    suffix = "_dryrun" if args.dry_run else ""
    run_dir = os.path.join(base_out,
                           "watch_" + datetime.now().strftime("%Y-%m-%d_%H%M%S") + suffix)
    os.makedirs(run_dir, exist_ok=True)
    spin.setup_logging(run_dir, args.verbose)
    LOG.info("run folder: %s", run_dir)

    obs = spin.ObsSession(host=obs_cfg.get("host", "localhost"),
                          port=int(obs_cfg.get("port", 4455)),
                          password=obs_cfg.get("password", ""))
    watch = None
    exit_code = spin.EXIT_OK
    stopped_by = ""

    try:
        obs.ensure_running(exe_path=obs_cfg.get("exe_path"))
        obs.connect()
        obs.check_format(img_format)
        obs.check_source(source)

        game = winfocus.find_window(game_cfg["process"],
                                    game_cfg.get("window_class", "UnityWndClass"))
        LOG.info("game window: %s", game)
        winfocus.ensure_restored(game)

        # The game log is the one thing this cannot do without: it is the only record of an
        # action taken on the touchscreen, and the only thing that says when one is finished.
        watcher = gamelog.GameLogWatcher(game_cfg.get("log") or gamelog.DEFAULT_LOG)
        LOG.info("game log:  %s", watcher.path)

        # The panel is optional here, unlike in spin.py -- nothing is being clicked, so it is
        # only needed to put a name to a press. A watch with no panel still sees every action
        # the game logs; it just reports "position 12" instead of "Rebet".
        panel = press_log = deck_window = None
        try:
            panel = ideck.load_panel(ideck_cfg.get("layout"), ideck_cfg.get("actions"))
            press_log = ideck.PressWatcher(ideck_cfg.get("log", ideck.DEFAULT_LOG))
            LOG.info("press log: %s", press_log.path)
        except ideck.IdeckError as exc:
            LOG.warning("WARNING: %s", exc)
            LOG.warning("         carrying on from the game log alone -- deck presses will only "
                        "be seen through what they cause")
        try:
            # Only for the record: nothing is clicked here, so the panel's window is not needed.
            deck_window = ideck.find_window(
                ideck_cfg.get("process", ideck.DEFAULT_PROCESS),
                ideck_cfg.get("window_class", ideck.DEFAULT_WINDOW_CLASS))
            LOG.info("i-Deck window: %s", deck_window)
        except ideck.IdeckError as exc:
            LOG.debug("no i-Deck window: %s", exc)

        state = gamelog.current_state(watcher.path)
        if panel:
            for line in ideck.report(panel, state):
                LOG.info("%s", line)
        LOG.info("bet now: %s (denomination %s)", state.get("bet") or "not seen in the log tail",
                 state.get("denom") or "?")

        native = spin.capture_size(obs, scene, source, game)
        size = spin.requested_size(native, capture_cfg)

        if args.dry_run:
            path = obs.screenshot(source, os.path.join(run_dir, f"dryrun.{img_format}"),
                                  *size, img_format, int(capture_cfg.get("quality", -1)))
            LOG.info("saved %s", path)
            LOG.info("dry run OK -- OBS, the game window, the game log%s and screenshotting all "
                     "work. Next: run it without --dry-run and play a spin",
                     ", the panel layout and the press log" if press_log else "")
            return spin.EXIT_OK

        watch = Watch(obs, cfg, run_dir, game, panel, watcher, press_log, size, native, state,
                      milestones=not args.no_milestones)
        watch.header = {
            "run_dir": run_dir,
            "started_at": datetime.now().isoformat(timespec="milliseconds"),
            "obs_version": obs.version,
            "game_window": repr(game),
            "ideck": ideck.describe(panel, deck_window, state) if panel else None,
            "game_log": watcher.path,
            "press_log": press_log.path if press_log else None,
            "capture_size": f"{size[0]}x{size[1]}",
            "native_size": f"{native[0]}x{native[1]}",
            "image_format": img_format,
            "image_quality": watch.quality,
            "deck_at_start": state.get("deck"),
            "settings": {"idle_timeout_s": watch.idle_timeout, "quiet_s": watch.quiet_s,
                         "after_delay_ms": int(watch.after_delay * 1000),
                         "tail_quiet_s": watch.tail_quiet, "action_timeout_s": watch.ceiling,
                         "long_wait_s": watch.long_wait_s,
                         "player_wait_s": watch.player_wait_s, "preroll_s": watch.preroll_s,
                         "milestone_shots": watch.milestones},
        }
        # Anything the logs said before now belongs to whoever was playing before we started.
        watcher.mark()
        if press_log:
            press_log.mark()

        LOG.info("watching. Play the game -- every round gets a folder under rounds/. "
                 "Ctrl-C to stop.")
        try:
            stopped_by = watch.run(args.duration, args.max_rounds)
        except KeyboardInterrupt:
            # The documented way to end a watch, so not an error: finish the open round, write
            # the session out, and exit 0.
            stopped_by = "Ctrl-C"
            LOG.info("stopping")
        watch.finish()
        watch.write(stopped_by)
        LOG.info("captured %d round%s: %s", len(watch.done),
                 "" if len(watch.done) == 1 else "s",
                 ", ".join(f"{n} {k}" for k, n in watch.counts().items()) or "nothing")

    except KeyboardInterrupt:
        LOG.warning("interrupted before the watch started")
        exit_code = spin.EXIT_ABORTED
    except (ObsError, winfocus.WindowNotFound, ideck.IdeckError, gamelog.GameLogError,
            RuntimeError) as exc:
        LOG.error("error: %s", exc)
        exit_code = spin.EXIT_ERROR
    except Exception:
        LOG.exception("unexpected failure")
        exit_code = spin.EXIT_ERROR
    finally:
        obs.close()
        if watch is not None:
            shutil.rmtree(watch.preroll_dir, ignore_errors=True)
        spin.prune_empty(run_dir)
        if os.path.isdir(run_dir):
            LOG.info("output in %s", run_dir)

    return exit_code


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
