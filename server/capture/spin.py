#!/usr/bin/env python
"""Capture one spin of HuffNPuffLink, start to finish.

    python -m capture.spin              # the real thing: before, spin, wait, after
    python -m capture.spin --dry-run    # report everything and shoot one frame, press nothing

What it does, in order:

1. Opens OBS if it isn't already open, and connects to its websocket.
2. Finds the game window and the i-Deck window (the "Virtual OLED"), and reports everything
   knowable about the panel -- its layout file, its 14 buttons with the positions the service
   logs them under, and what the deck is currently offering, read out of the game's own log.
3. Starts OBS recording into the run folder, screenshots the screen before the spin, clicks
   Repeat Bet on the i-Deck, then waits for the *game* to say the spin is finished -- however
   long that takes -- screenshots the result, and **if the spin won, takes the win on the glass
   and screenshots that too**, then stops the recording.

So a spin captures two frames, or three if it won (`server.frames` names them):

    pre_spin       the meters before the wager
    spin_result    the outcome, WIN meter showing what it paid
    win_collected  after TAKE WIN -- the win is now in the cash meter. Wins only

The third frame is not a nicety. A win is announced but **not paid**: the game parks on the
collect/gamble offer and holds the money there, so `spin_result` shows a cash meter with the bet
taken off and nothing added back. The spin's money is only settled once the win is collected, and
the i-Deck cannot do that without betting again -- Repeat Bet reads "Collect Win" and means
*collect **and** bet*. So the collect is a click on the game's own TAKE WIN (`gameclick`),
confirmed against the game log, and the frame after it is the spin's final state.

The wait is the point. An ordinary spin takes ~3.3 s but a Hold & Spin feature ran 53 s over 23
free spins, so no fixed delay can be right for both; the game log is asked instead. Every event
it reports is written to spin.json with the game's own timestamp.

There are two waits, not one, because a win is *announced* before it is *displayed*: the game logs
the collect/gamble offer at the start of the win meter's count-up, so a spin that ends on a win
waits a second time for the game to say the meters have stopped moving (`await_meters`). Without
it, 28% of winning spins were photographed mid-count-up, and one recorded a win of 1.49 on a spin
that paid 12.00.

The video covers the same stretch as the two frames and a little either side, and lands beside
them as spin.mkv (whatever container OBS is set to). It is a bonus, not the point: every way it
can fail is a warning, and the run carries on without it. `--no-record` or `"record": {"enabled":
false}` turns it off.
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

from .. import frames
from ..settings import DEFAULT_CONFIG, load_config, resolve

from . import gameclick, gamelog, ideck, winfocus
from .obs_client import MAX_DIM, ObsError, ObsSession, Recording, clamp_dim

LOG = logging.getLogger("spin")

EXIT_OK, EXIT_ERROR, EXIT_ABORTED = 0, 1, 2

# A game window smaller than this in either axis isn't finished starting, or is minimised.
MIN_CLIENT_DIM = 100

# The first thing a new spin logs. `bet_locked` normally comes first, but either will do.
SPIN_BEGINS = ("bet_locked", "spin_started")

# -- the waits -------------------------------------------------------------
# These are measurements, not preferences, so they live here beside the code that acts on
# them rather than in config.json. Every one of them is justified in this module's
# docstring and in CLAUDE.md's "Timing invariants"; changing one means re-measuring
# against real log history, which a config edit invites and a constant does not. There is
# no fixed delay among them -- AFTER_DELAY_MS is the sole deliberate sleep.

# Only a backstop. An ordinary spin is ~3.3 s and a Hold & Spin was measured at 53 s, so
# nothing should ever reach this.
TIMEOUT_S = 180.0
# The one deliberate sleep, letting the last frame settle before the after shot.
AFTER_DELAY_MS = 800
# How long to keep reading past a `win` for the meters to settle. 90 s against a measured
# worst case of 44.8 s. 0 turns it off and restores the old behaviour of shooting
# AFTER_DELAY_MS after the win was announced.
METER_SETTLE_S = 90.0
# How long to wait for the game over that ends a separate collect. 60 s against a measured
# worst case of 15.47 s over 27 collects; see take_win.
COLLECT_TIMEOUT_S = 60.0
# On, because a win that is never collected leaves the spin's money unsettled and the final
# frame showing a cash meter the win was never paid into. `--no-collect` turns it off.
COLLECT_AFTER_SPIN = True
# Off. `--collect-first` turns it on, to clear a win something *else* left pending.
COLLECT_BEFORE_BET = False


# -- setup -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m capture.spin", description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                       help="check everything and save one screenshot, without spinning")
    parser.add_argument("--no-record", action="store_true",
                       help="skip the OBS video recording; capture only the two frames")
    parser.add_argument("--no-collect", action="store_true",
                       help="don't take the win at the end of a winning spin. Leaves it on the "
                            "collect/gamble offer, so the run captures two frames instead of "
                            "three and its last frame shows cash the win was never paid into")
    parser.add_argument("--collect-first", action="store_true",
                       help="if a win is pending, take it by clicking the game's own TAKE WIN "
                            "before betting, instead of letting the i-Deck press collect and "
                            "bet in one. Keeps the collect out of this spin's before/after pair")
    parser.add_argument("--out", help="base folder that run folders are created in")
    parser.add_argument("--run-dir",
                       help="write into exactly this folder, instead of composing "
                            "<out>/<timestamp>. The server passes it so it knows the run "
                            "folder before this process starts, rather than guessing at "
                            "the newest one -- the timestamp is only second-granular, so "
                            "two runs a second apart would collide.")
    parser.add_argument("--config", default=None,
                       help="path to config.json (default: server/config.json)")
    parser.add_argument("-v", "--verbose", action="store_true",
                       help="debug logging (always written to run.log regardless)")
    return parser


class _ScrubSecrets(logging.Filter):
    """Drop the obsws-python line that logs the OBS password in plaintext.

    On connect the SDK logs "Connecting with parameters: ... password='...'" at INFO. That must
    not end up in run.log.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not (record.name.startswith("obsws_python")
                    and "password=" in record.getMessage())


def setup_logging(run_dir: str, verbose: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console)

    logfile = logging.FileHandler(os.path.join(run_dir, "run.log"), encoding="utf-8")
    logfile.setLevel(logging.DEBUG)
    logfile.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s"))
    logfile.addFilter(_ScrubSecrets())
    root.addHandler(logfile)

    # The SDK logs a traceback for every failed request, including ones we probe for and
    # recover from. Keep that in run.log and off the console.
    sdk = logging.getLogger("obsws_python")
    sdk.setLevel(logging.DEBUG if verbose else logging.WARNING)
    sdk.propagate = False
    sdk.addHandler(logfile)


def prune_empty(run_dir: str) -> None:
    """Delete a run folder that captured nothing, so failed attempts don't pile up."""
    if set(os.listdir(run_dir)) - {"run.log"}:
        return
    # Windows won't delete run.log while a handler holds it open.
    for logger in (logging.getLogger(), logging.getLogger("obsws_python")):
        for handler in list(logger.handlers):
            if isinstance(handler, logging.FileHandler):
                logger.removeHandler(handler)
                handler.close()
    shutil.rmtree(run_dir, ignore_errors=True)


# -- capture ---------------------------------------------------------------


def capture_size(obs: ObsSession, scene: str, source: str, window, attempts=12, delay=0.5):
    """The capture source's own native size, once it is genuinely capturing.

    Don't trust the first answer. A source still acquiring the window reports a transient
    garbage size -- 185x9 was seen right after an OBS cold start, which clears OBS's own 8px
    floor and yields a useless PNG while everything claims success. So wait for a size that is
    plausible against the game window's own client area.
    """
    client_w = client_h = 0
    last = None
    for attempt in range(1, attempts + 1):
        client_w, client_h = winfocus.client_size(window)
        if client_w >= MIN_CLIENT_DIM and client_h >= MIN_CLIENT_DIM:
            last = obs.resolve_source_size(scene, source)
            # OBS reports physical pixels, so with display scaling it can report *more* than
            # the logical client size; only guard against it reporting far less.
            if last and last[0] >= client_w // 2 and last[1] >= client_h // 2:
                LOG.info("source %r is capturing at %dx%d", source, *last)
                return last
        LOG.debug("attempt %d/%d: window client %dx%d, OBS reports %s",
                  attempt, attempts, client_w, client_h, last)
        time.sleep(delay)

    if client_w >= MIN_CLIENT_DIM and client_h >= MIN_CLIENT_DIM:
        # Clamped like resolve_source_size does: OBS rejects a screenshot request outside its
        # own dimension range, so an oversized window must not be passed through raw.
        fallback = clamp_dim(client_w), clamp_dim(client_h)
        LOG.warning("WARNING: OBS never reported a usable size for %r (last: %s); falling back "
                    "to the game window's own %dx%d", source, last, *fallback)
        return fallback
    raise RuntimeError(f"the game window reports a {client_w}x{client_h} client area -- it "
                       "looks minimised or still starting, so OBS has nothing to capture")


def requested_size(native, capture_cfg: dict) -> tuple[int, int]:
    """The size to ask OBS for, from the source's native size and the capture settings.

    OBS renders the source into a texture of whatever size the request names, independently of
    the scene and the canvas -- so a screenshot is never limited by the OBS output resolution,
    only by OBS's own 4096 px ceiling and by what the window actually has. Past `native`,
    `capture.scale` buys bigger pixels rather than more detail; genuinely sharper frames come
    from a bigger game window or a higher desktop resolution. Both are said out loud rather than
    left for someone to discover from a blurry PNG.

    `capture.width`/`capture.height` win over `capture.scale`, and giving only one of them keeps
    the source's aspect ratio instead of stretching it.
    """
    native_w, native_h = int(native[0]), int(native[1])
    width, height = native_w, native_h
    explicit_w = capture_cfg.get("width") or 0
    explicit_h = capture_cfg.get("height") or 0
    scale = float(capture_cfg.get("scale") or 1)

    if explicit_w and explicit_h:
        width, height = int(explicit_w), int(explicit_h)
    elif explicit_w:
        width = int(explicit_w)
        height = round(native_h * width / native_w) if native_w else native_h
    elif explicit_h:
        height = int(explicit_h)
        width = round(native_w * height / native_h) if native_h else native_w
    elif scale != 1:
        width, height = round(native_w * scale), round(native_h * scale)

    if max(width, height) > MAX_DIM:
        # Both axes by the same factor: clamping them independently would squash the picture.
        factor = MAX_DIM / max(width, height)
        size = clamp_dim(round(width * factor)), clamp_dim(round(height * factor))
        LOG.warning("WARNING: OBS caps a screenshot at %d px, so %dx%d was scaled to %dx%d",
                    MAX_DIM, width, height, *size)
    else:
        size = clamp_dim(width), clamp_dim(height)
    if size != (native_w, native_h):
        LOG.info("capturing at %dx%d (source is %dx%d)", *size, native_w, native_h)
        if size[0] > native_w or size[1] > native_h:
            LOG.warning("WARNING: that is larger than the source, so OBS is upscaling -- bigger "
                        "files, no more detail. For genuinely sharper frames make the game "
                        "window bigger (or raise the desktop resolution) and leave "
                        "capture.scale at 1.")
    return size


def shot(obs: ObsSession, source: str, path: str, size, img_format: str,
         quality: int = -1) -> dict:
    # Stamped just before the request, which is as close as we can get to the frame OBS grabs.
    entry = {"wall_clock": datetime.now().isoformat(timespec="milliseconds")}
    obs.screenshot(source, path, *size, img_format, quality)
    entry["file"] = os.path.basename(path)
    entry["bytes"] = os.path.getsize(path)
    return entry


# -- following the spin ----------------------------------------------------


def _row(event: gamelog.Event) -> dict:
    """One game-log event as a JSON-serialisable row, with the game's own clock."""
    row = {"event": event.name,
           "game_clock": event.at.strftime("%H:%M:%S.%f")[:-3] if event.at else None,
           "note": gamelog.describe(event)}
    row.update(event.fields)
    return row


def follow_spin(watcher: gamelog.GameLogWatcher, idle_timeout: float, ceiling: float,
                carry: bool) -> tuple[list[dict], str | None]:
    """Read the game log until the spin is over. Returns its events and the terminal one.

    `carry` says a win was already pending when we pressed. Repeat Bet reads "Collect Win" in
    that state and means "collect, then bet again", so our press resolves the *previous* spin
    first and its `game_over` arrives a second later, belonging to that older spin. Without
    skipping it, this spin would be declared finished about a second in and the after shot would
    catch the reels still turning.

    The whole trail of that collect belongs to the old spin -- `take_win`, the gamble states,
    its `game_over`, and its `final_grid` -- so those are tagged rather than dropped, and the
    summary ignores them. Otherwise a spin that went on to win nothing would be reported as
    having chosen Take Win, with the previous spin's grid attached.

    The boundary is where the *new* spin begins, not where the old one ends: the old spin's
    `final_grid` is logged a few milliseconds **after** its `game_over`, so ending the carry on
    game_over let its reel stops through. If the collect turns out not to start a new spin at
    all, nothing here hangs -- the idle timeout expires and the spin is reported unfinished.
    """
    seen: list[dict] = []
    terminal = None
    for event in watcher.drain(idle_timeout, ceiling):
        record = _row(event)

        if carry and event.name not in SPIN_BEGINS:
            record["belongs_to"] = "the previous, uncollected win"
            seen.append(record)
            LOG.info("   %s  %s (previous spin)", record["game_clock"], record["note"])
            continue
        carry = False

        seen.append(record)
        LOG.info("   %s  %s", record["game_clock"], record["note"])
        if event.name in gamelog.TERMINAL:
            terminal = event.name
            break
    return seen, terminal


def await_meters(watcher: gamelog.GameLogWatcher,
                 settle_s: float) -> tuple[list[dict], str | None]:
    """Keep reading until the game says the meters have stopped moving. Only used after a `win`.

    `win` -- the collect/gamble offer -- is logged when the win is *announced*, at the start of the
    win meter's count-up rather than the end of it, so it is the one terminal event that fires
    while the screen is still changing. Measured over the 520 rounds in this cabinet's two logs,
    `win` to `results_done` is 0.33 s median, 6.3 s at p90 and 44.8 s at worst: comfortably inside
    `after_delay_ms` most of the time, and outside it on **28%** of winning spins. Run
    2026-08-10_163826 is what that costs -- `win` at 16:38:32.987, the after shot 839 ms later,
    the meters settling at 16:38:39.300, and a recorded win of 1.49 on a spin that paid 12.00.

    Raising `after_delay_ms` is the wrong fix twice over: 45 s of sleep on every spin to cover the
    worst case, and still no guarantee. So the game is asked, the same way the spin itself is.

    Nothing here can hang waiting for a press we never make. `results_done` arrived before the
    player's collect in 112/113 winning rounds that were collected at all, and the one exception
    beat it by 2 ms -- a player interrupting the count-up, which the spin button is documented to
    do. It is also why this is bounded by a flat `settle_s` rather than by an idle timeout that
    restarts: this waits for **one specific marker** that measurement says arrives within 44.8 s,
    not for an open-ended feature, and the log went silent for 43.8 s of one of those waits, which
    any idle timeout worth having would have given up on.
    """
    seen: list[dict] = []
    settled = None
    for event in watcher.drain(settle_s, settle_s):
        record = _row(event)
        seen.append(record)
        LOG.info("   %s  %s", record["game_clock"], record["note"])
        if event.name in gamelog.SETTLED:
            settled = event.name
            break
    return seen, settled


def take_win(window, watcher: gamelog.GameLogWatcher, game_cfg: dict, click_cfg: dict,
             timeout_s: float) -> dict:
    """Collect a win on the glass, and wait for the game to finish doing it.

    This is the only reason `gameclick` exists, and it is used at two different moments for the
    same reason: the i-Deck has no separate TAKE WIN. With a win pending, Rebet reads "Collect
    Win" and means *collect **and** bet again*, so the deck cannot settle a win without starting
    another spin, and any pair of frames spanning that press has a collect hidden inside it.

    At the **end** of a spin (the ordinary case, `spin.collect_after_spin`) this is what makes the
    spin's money final: a win is announced but not paid, so `spin_result` shows cash with the bet
    taken off and nothing added back, and the third frame after this call is the one where the
    win is actually in the cash meter. At the **start** (`--collect-first`) it clears a win that
    something else left pending, so the run does not begin mid-transaction.

    **Waits for `game_over`, not for `gamelog.SETTLED`.** That is the opposite of `await_meters`
    and it is measured, not assumed: over the 27 collects in this cabinet's two logs, `decline`
    to `GameOverMsg` runs 0.20 s median, 0.47 s at p90 and **15.47 s** at worst, and
    `results_done` appears in between on **0 of 27** of them. Handing this to `await_meters`
    would wait out the whole 90 s of `spin.meter_settle_s` every time and then report that
    nothing settled. `game_over` is what ends a collect -- the same event `watch.actions`
    already uses as its round boundary, and for the same reason.

    The bound is flat rather than an idle timeout that restarts, for the reason `await_meters`
    is: this waits for one specific marker with a measured worst case, not for an open-ended
    feature.

    `game_cfg` is the active game's block (`cfg["game"]`), because the target is per *game*,
    not per window size -- see `gameclick.targets_for`. The point that collects a win in one game
    lands on the wallpaper of another, and the failure is a run that ends with the win still on
    the offer. `click_cfg` is `config.json`'s `game_click`, which is about *delivery* and is the
    same whatever game is running.
    """
    process = game_cfg.get("process")
    targets = gameclick.targets_for(game_cfg)
    if "take_win" not in targets:
        raise gameclick.GameClickError(
            f"there is no \"take_win\" target for {process} in game_config.json, so the win "
            "cannot be collected on the glass. Measure one with "
            "`python -m server.capture.gameclick --calibrate` while a win is pending.")

    record = gameclick.deliver(
        window, watcher, targets["take_win"],
        method=click_cfg.get("click_method", gameclick.CLICK_METHOD),
        hold_ms=gameclick.CLICK_HOLD_MS,
        foreground=bool(click_cfg.get("foreground", True)),
        confirm_timeout=gameclick.CONFIRM_TIMEOUT_S,
        expect="take_win")
    if not record["landed"]:
        # Fatal rather than a warning, both ways round. At the end of a spin a failed collect
        # means the final frame would show a win that is still sitting on the offer, and the
        # ledger would be short by exactly that win; at the start it means spinning with the old
        # win still pending, which is the conflated pair this avoids.
        raise gameclick.GameClickError(
            "could not collect the win by clicking the game: " + record["reading"])
    LOG.info("collected the win on the glass at %s (%s)", record["point"], record["method"])

    followed: list[dict] = []
    for event in watcher.drain(timeout_s, timeout_s):
        row = _row(event)
        followed.append(row)
        LOG.info("   %s  %s", row["game_clock"], row["note"])
        if event.name == "game_over":
            break
    record["events"] = record["events"] + [r["event"] for r in followed]
    record["completed"] = any(r["event"] == "game_over" for r in followed)
    if not record["completed"]:
        LOG.warning("WARNING: the game never logged a game over within %.0fs "
                    "(spin.collect_timeout_s) after the collect, so the cash meter may still "
                    "be counting up in the before frame -- treat its balance as unverified.",
                    timeout_s)
    return record


def classify(events: list[dict]) -> dict:
    """What kind of spin that was, from the events it produced.

    A spin is a loss, or a win of one of several kinds, and the kinds are not exclusive -- a
    Hold & Spin can award a jackpot and still end on a gamble offer -- so this reports each
    independently rather than picking one label.

    Everything tagged `belongs_to` is ignored: those events are the previous, uncollected win
    being resolved by our press, and counting them would attribute its feature and its grid to
    this spin.
    """
    mine = [e for e in events if not e.get("belongs_to")]
    names = [e["event"] for e in mine]

    # One entry per reel set that came to rest, so a Hold & Spin contributes one per free spin.
    # `final_grid` is the game's own last word on the grid and repeats the final `reels_stopped`,
    # so it is taken as the answer rather than appended as another set.
    reel_stops: list[list[int]] = []
    final_stops = None
    for record in mine:
        stops = [int(n) for n in record["stops"].split()] if record.get("stops") else None
        if not stops:
            continue
        if record["event"] == "reels_stopped":
            reel_stops.append(stops)
        elif record["event"] == "final_grid":
            final_stops = stops
    # A win breaks out on `win`, before the game logs `final_grid`, so fall back to the reels.
    if final_stops is None and reel_stops:
        final_stops = reel_stops[-1]

    summary = {
        "won": "win" in names,
        "free_spins": sorted({e["feature"] for e in mine
                              if e["event"] == "feature_triggered" and e.get("feature")}),
        "hold_and_spin": "hold_and_spin_started" in names or "hold_and_spin_prompt" in names,
        "jackpot": "jackpot_awarded" in names or "jackpot_celebration" in names,
        "wager_saver": "wager_saver_offered" in names,
        "wager_saver_accepted": "wager_saver_accepted" in names,
        "mystery_reveal": "mystery_reveal" in names,
        # Only meaningful once a win has been offered; None means the choice wasn't reached.
        "chose": ("gamble" if "gamble_played" in names
                  else "take_win" if "take_win" in names else None),
        "reel_stops": reel_stops,
        "final_stops": final_stops,
    }
    kinds = [k for k, present in (("free spins", summary["free_spins"]),
                                  ("hold and spin", summary["hold_and_spin"]),
                                  ("jackpot", summary["jackpot"]),
                                  ("wager saver", summary["wager_saver"])) if present]
    summary["outcome"] = ("win" if summary["won"] else "no win") + (
        " -- " + ", ".join(kinds) if kinds else "")
    return summary


# -- the run ---------------------------------------------------------------


def run(args) -> int:
    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read config {args.config or DEFAULT_CONFIG}: {exc}",
              file=sys.stderr)
        return EXIT_ERROR

    obs_cfg = cfg.get("obs", {})
    capture_cfg = cfg.get("capture", {})
    # The active game, resolved by settings.load_config out of game_config.json: its
    # process, window class, log and click targets, already agreed with each other.
    game_cfg = cfg["game"]
    # How a click on the game window is delivered. Not per game -- see gameclick.
    click_cfg = cfg.get("game_click", {})
    ideck_cfg = cfg.get("ideck", {})
    record_cfg = cfg.get("record", {})

    scene = capture_cfg.get("scene", "Scene")
    source = capture_cfg.get("source", "Window Capture")
    img_format = capture_cfg.get("format", "png")
    quality = int(capture_cfg.get("quality", -1))
    idle_timeout = gamelog.IDLE_TIMEOUT_S
    ceiling = TIMEOUT_S
    after_delay = AFTER_DELAY_MS / 1000.0
    meter_settle = METER_SETTLE_S
    collect_timeout = COLLECT_TIMEOUT_S
    collect_first = args.collect_first or COLLECT_BEFORE_BET
    collect_after_spin = (not args.no_collect) and COLLECT_AFTER_SPIN
    if args.run_dir:
        # Named by the caller, so it can find the artefacts without racing the timestamp.
        # `resolve` anchors a relative one on `server/`, not on the CWD -- the server
        # passes an absolute path, but a person running this by hand from anywhere gets
        # the same folder the CLI and the UI read.
        run_dir = resolve(args.run_dir)
    else:
        base_out = resolve(args.out or cfg.get("output", {}).get("dir", "captures"))
        suffix = "_dryrun" if args.dry_run else ""
        run_dir = os.path.join(base_out, datetime.now().strftime("%Y-%m-%d_%H%M%S") + suffix)
    os.makedirs(run_dir, exist_ok=True)
    setup_logging(run_dir, args.verbose)
    LOG.info("run folder: %s", run_dir)

    obs = ObsSession(host=obs_cfg.get("host", "localhost"),
                     port=int(obs_cfg.get("port", 4455)),
                     password=obs_cfg.get("password", ""))
    exit_code = EXIT_OK
    # A dry run presses nothing, so there is nothing to film.
    recorder = None
    if record_cfg.get("enabled", True) and not args.no_record and not args.dry_run:
        recorder = Recording(obs, run_dir)

    try:
        # 1. OBS, opened if it isn't already.
        obs.ensure_running(exe_path=obs_cfg.get("exe_path"))
        obs.connect()
        obs.check_format(img_format)
        obs.check_source(source)

        # 2. The two windows, and everything the panel will tell us about itself.
        # The process name is kept, not just used: it is what named the game's block in
        # game_config.json, and it goes into spin.json so a run says which game it was.
        game_process = game_cfg["process"]
        game = winfocus.find_window(game_process,
                                    game_cfg.get("window_class", "UnityWndClass"))
        LOG.info("game window: %s", game)
        # A minimised window gives OBS no frames to capture.
        winfocus.ensure_restored(game)

        panel = ideck.load_panel(ideck_cfg.get("layout"), ideck_cfg.get("actions"))
        deck_window = ideck.find_window(ideck_cfg.get("process", ideck.DEFAULT_PROCESS),
                                        ideck_cfg.get("window_class", ideck.DEFAULT_WINDOW_CLASS))
        LOG.info("i-Deck window: %s", deck_window)
        watcher = gamelog.GameLogWatcher(game_cfg.get("log") or gamelog.DEFAULT_LOG)
        press_log = ideck.PressWatcher(ideck_cfg.get("log", ideck.DEFAULT_LOG))
        LOG.info("game log:  %s", watcher.path)
        LOG.info("press log: %s", press_log.path)

        state = gamelog.current_state(watcher.path)
        for line in ideck.report(panel, state):
            LOG.info("%s", line)

        warning = winfocus.elevation_warning(deck_window)
        if warning:
            LOG.warning("WARNING: %s", warning)

        # Refuse before shooting anything: a screensaver makes the press impossible, and failing
        # here leaves no half-finished run folder behind.
        blocked = ideck.input_blocked()
        if blocked:
            if not args.dry_run:
                raise ideck.IdeckError(blocked)
            LOG.warning("WARNING: %s", blocked)

        button_spec = ideck_cfg.get("button", "spin")
        button = panel.button(button_spec)
        native = capture_size(obs, scene, source, game)
        size = requested_size(native, capture_cfg)

        if args.dry_run:
            path = obs.screenshot(source, os.path.join(run_dir, f"dryrun.{img_format}"),
                                  *size, img_format, quality)
            LOG.info("saved %s", path)
            LOG.info("dry run OK -- OBS, both windows, the panel layout, the game log and "
                     "screenshotting all work. It would press %s (position %d). Next: run it "
                     "without --dry-run", button.name, button.position)
            # Resolved rather than read straight out of the file, so a game block with no
            # take_win says so here -- before a real run discovers it by clicking the
            # wallpaper with a win standing on the offer.
            try:
                target = gameclick.targets_for(game_cfg).get("take_win")
            except gameclick.GameClickError as exc:
                target = None
                LOG.warning("WARNING: %s", exc)
            named = target or (f"<nothing -- there is no \"take_win\" for {game_process}. "
                               "Measure one with `gameclick --calibrate`>")
            if collect_after_spin:
                LOG.info("if the spin wins, the win would then be taken by clicking %s, giving a "
                         "third frame (%s.%s)", named, frames.WIN_COLLECTED, img_format)
            else:
                LOG.info("--no-collect: a win would be left on the offer, so this run would "
                         "capture two frames and its last would show unpaid cash")
            if collect_first:
                LOG.info("--collect-first is on: a *previous* pending win would be cleared by "
                         "clicking %s before betting. Right now there is %s.", named,
                         "one pending" if state.get("gamble") == "offerState"
                         else "none pending, so it would change nothing")
            return EXIT_OK

        # 3. Rolling, before, spin, wait for the game to finish it, after.
        if recorder is not None:
            recorder.start(scene, source)

        # A win left uncollected -- by a previous run or by hand -- changes what our press does.
        carry = state.get("gamble") == "offerState"
        stale_win = stale_collect = None
        if carry and collect_first:
            LOG.info("a win is pending and the collect is being taken separately, on the glass, "
                     "so the before frame is a settled screen and this spin's ledger has no "
                     "collect folded into it")
            # Shot before the click, not after: the post-collect screen is what before.* is
            # about to be, so the frame that would otherwise be lost is the one showing the win
            # still standing. It is what makes the collect itself auditable.
            stale_win = shot(obs, source, os.path.join(run_dir, f"stale_win.{img_format}"),
                             size, img_format, quality)
            stale_collect = take_win(game, watcher, game_cfg, click_cfg, collect_timeout)
            carry = False
            # The game over lands while the last frame of the count-up is still being drawn --
            # the same reason the after shot waits.
            time.sleep(after_delay)

        pre_spin = shot(obs, source,
                        os.path.join(run_dir, f"{frames.PRE_SPIN}.{img_format}"),
                        size, img_format, quality)
        if carry:
            LOG.info("a win is pending, so this press collects it and bets again")

        started = time.monotonic()
        # Mark the log before pressing, or an event landing during the press is missed.
        watcher.mark()
        pressed_at = datetime.now().isoformat(timespec="milliseconds")
        ideck.press(panel, deck_window, button_spec,
                    hold_ms=ideck.CLICK_HOLD_MS, watcher=press_log,
                    confirm_timeout=ideck.CONFIRM_TIMEOUT_S)
        LOG.info("pressed %s (position %d) -- confirmed in the panel log; waiting for the game "
                 "to finish the spin", button.name, button.position)

        events, terminal = follow_spin(watcher, idle_timeout, ceiling, carry)
        if terminal is None:
            # Two limits end the wait, and saying the wrong one sends the reader to the wrong
            # setting: the ceiling means the spin was still going, the idle timeout means it
            # went quiet without an outcome.
            if time.monotonic() - started >= ceiling:
                LOG.warning("WARNING: the spin was still running when the %.0fs ceiling expired "
                            "(spin.timeout_s). Shooting anyway; the frame may be mid-feature.",
                            ceiling)
            else:
                LOG.warning("WARNING: the game logged nothing for %.0fs without reporting an "
                            "outcome (gamelog.idle_timeout_s). Shooting anyway; the frame may "
                            "be mid-animation.", idle_timeout)
        # A win is announced at the *start* of the meter's count-up, so `win` on its own is not
        # "the spin is over" -- see await_meters. A `game_over` needs none of this: it already
        # lands after the results display finished, on 404/404 losing rounds replayed here.
        settled = None
        if terminal == "win" and meter_settle > 0:
            LOG.info("the win meter is counting up; waiting for the game to say it has finished")
            extra, settled = await_meters(watcher, meter_settle)
            events.extend(extra)
            if settled is None:
                LOG.warning("WARNING: the game never said the meters had settled within %.0fs "
                            "(spin.meter_settle_s). Shooting anyway; the WIN meter may still be "
                            "counting up, so treat the amount in %s.%s as unverified.",
                            meter_settle, frames.SPIN_RESULT, img_format)
        # The terminal event fires when the game decides the spin is over, while the last frame
        # of it is still being drawn.
        time.sleep(after_delay)
        spin_result = shot(obs, source,
                           os.path.join(run_dir, f"{frames.SPIN_RESULT}.{img_format}"),
                           size, img_format, quality)

        # The third frame, and the reason it exists: `spin_result` shows a cash meter with the
        # bet taken off and the win *not* added, because the game holds a win on the
        # collect/gamble offer instead of paying it. So a winning spin is not finished here --
        # take the win on the glass and photograph the meter that results. That frame is the
        # spin's final state and what the ledger is closed against.
        win_collected = win_collect = None
        if terminal == "win" and collect_after_spin:
            LOG.info("the spin won, so the win is being taken on the glass to settle it")
            win_collect = take_win(game, watcher, game_cfg, click_cfg, collect_timeout)
            time.sleep(after_delay)
            win_collected = shot(obs, source,
                                 os.path.join(run_dir, f"{frames.WIN_COLLECTED}.{img_format}"),
                                 size, img_format, quality)
        elif terminal == "win":
            LOG.warning("WARNING: the spin won but the win was left on the offer "
                        "(spin.collect_after_spin is off), so %s.%s shows a cash meter the win "
                        "has not been paid into yet.", frames.SPIN_RESULT, img_format)

        measured = round(time.monotonic() - started, 3)
        # Stopped here rather than in the teardown, so the video's own details make it into
        # spin.json. Stopping it twice is harmless.
        video = recorder.stop() if recorder is not None else None

        summary = classify(events)
        record = {
            "run_dir": run_dir,
            "obs_version": obs.version,
            "game_window": repr(game),
            "ideck": ideck.describe(panel, deck_window, state),
            "game_log": watcher.path,
            "press_log": press_log.path,
            "capture_size": f"{size[0]}x{size[1]}",
            "native_size": f"{native[0]}x{native[1]}",
            "image_format": img_format,
            "image_quality": quality,
            "video": video,
            "button": {"name": button.name, "position": button.position,
                       "pressed_at": pressed_at},
            "collected_a_pending_win": carry,
            # The frames, keyed by the names in server.frames, which is what extract reads and
            # what the UI labels. `win_collected` is absent from a losing spin.
            "frames": {name: entry for name, entry in
                       ((frames.PRE_SPIN, pre_spin),
                        (frames.SPIN_RESULT, spin_result),
                        (frames.WIN_COLLECTED, win_collected)) if entry},
            # The click that settled this spin's win: the point, how it was delivered, and what
            # the game logged in answer -- so a verdict reached over these frames can be traced
            # back to the input behind it. None on a losing spin.
            "win_collect": win_collect,
            # Only when --collect-first found a win that something else had left pending. Not
            # part of the ledger: a diagnostic pair showing what was cleared before betting.
            "stale_win": stale_win,
            "stale_collect": stale_collect,
            "measured_s": measured,
            "terminal_event": terminal,
            # None on a losing spin (nothing to wait for) and on a win the game never settled.
            "meters_settled_by": settled,
            "idle_timeout_s": idle_timeout,
            "ceiling_s": ceiling,
            "meter_settle_s": meter_settle,
            **summary,
            "events": events,
        }
        with open(os.path.join(run_dir, "spin.json"), "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)

        # `measured` now includes the wait for the meters, which on a big win is most of it --
        # so say so, or a 50 s spin looks like a hang rather than a count-up.
        LOG.info("spin finished in %.2fs on %s%s: %s%s", measured, terminal or "a timeout",
                 f" then {settled}" if settled else "", summary["outcome"],
                 f", stops {summary['final_stops']}" if summary["final_stops"] else "")
        LOG.info("%d frames: %s", len(record["frames"]), ", ".join(
            f"{name}.{img_format}" for name in record["frames"]))

    except KeyboardInterrupt:
        LOG.warning("interrupted")
        exit_code = EXIT_ABORTED
    except (ObsError, winfocus.WindowNotFound, ideck.IdeckError, gamelog.GameLogError,
            gameclick.GameClickError, RuntimeError) as exc:
        LOG.error("error: %s", exc)
        exit_code = EXIT_ERROR
    except Exception:
        LOG.exception("unexpected failure")
        exit_code = EXIT_ERROR
    finally:
        # Before closing the socket, and before pruning: a recording still running would keep
        # writing into a folder that is about to go away.
        if recorder is not None:
            recorder.stop()
        obs.close()
        # Prune first, then point at the folder only if it survived -- naming a path that was
        # just deleted is worse than saying nothing.
        prune_empty(run_dir)
        if os.path.isdir(run_dir):
            LOG.info("output in %s", run_dir)

    return exit_code


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
