#!/usr/bin/env python
"""Capture one spin of HuffNPuffLink, start to finish.

    python spin.py                 # the real thing: before, spin, wait, after
    python spin.py --dry-run       # report everything and shoot one frame, press nothing

What it does, in order:

1. Opens OBS if it isn't already open, and connects to its websocket.
2. Finds the game window and the i-Deck window (the "Virtual OLED"), and reports everything
   knowable about the panel -- its layout file, its 14 buttons with the positions the service
   logs them under, and what the deck is currently offering, read out of the game's own log.
3. Screenshots the screen before the spin, clicks Repeat Bet on the i-Deck, then waits for the
   *game* to say the spin is finished -- however long that takes -- screenshots the result, and
   stops.

The wait is the point. An ordinary spin takes ~3.3 s but a Hold & Spin feature ran 53 s over 23
free spins, so no fixed delay can be right for both; the game log is asked instead. Every event
it reports is written to spin.json with the game's own timestamp.
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

import gamelog
import ideck
import winfocus
from obs_client import ObsError, ObsSession, clamp_dim

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = logging.getLogger("spin")

EXIT_OK, EXIT_ERROR, EXIT_ABORTED = 0, 1, 2

# A game window smaller than this in either axis isn't finished starting, or is minimised.
MIN_CLIENT_DIM = 100

# The first thing a new spin logs. `bet_locked` normally comes first, but either will do.
SPIN_BEGINS = ("bet_locked", "spin_started")


# -- setup -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spin.py", description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                       help="check everything and save one screenshot, without spinning")
    parser.add_argument("--out", help="base folder that run folders are created in")
    parser.add_argument("--config", default=os.path.join(HERE, "config.json"))
    parser.add_argument("-v", "--verbose", action="store_true",
                       help="debug logging (always written to run.log regardless)")
    return parser


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    # The environment wins, so the password need not be in a file at all.
    password = os.environ.get("OBS_WS_PASSWORD")
    if password:
        cfg.setdefault("obs", {})["password"] = password
    return cfg


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
    """Ask OBS how big the capture source is, once it is genuinely capturing.

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
                LOG.info("capturing %r at %dx%d", source, *last)
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


def shot(obs: ObsSession, source: str, path: str, size, img_format: str) -> dict:
    # Stamped just before the request, which is as close as we can get to the frame OBS grabs.
    entry = {"wall_clock": datetime.now().isoformat(timespec="milliseconds")}
    obs.screenshot(source, path, *size, img_format)
    entry["file"] = os.path.basename(path)
    entry["bytes"] = os.path.getsize(path)
    return entry


# -- following the spin ----------------------------------------------------


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
        record = {"event": event.name,
                  "game_clock": event.at.strftime("%H:%M:%S.%f")[:-3] if event.at else None,
                  "note": gamelog.describe(event)}
        record.update(event.fields)

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
        print(f"error: cannot read config {args.config}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    obs_cfg = cfg.get("obs", {})
    capture_cfg = cfg.get("capture", {})
    target_cfg = cfg.get("target", {})
    spin_cfg = cfg.get("spin", {})
    gamelog_cfg = cfg.get("gamelog", {})
    ideck_cfg = cfg.get("ideck", {})

    scene = capture_cfg.get("scene", "Scene")
    source = capture_cfg.get("source", "Window Capture")
    img_format = capture_cfg.get("format", "png")
    idle_timeout = float(gamelog_cfg.get("idle_timeout_s", 8.0))
    ceiling = float(spin_cfg.get("timeout_s", 180.0))
    after_delay = float(spin_cfg.get("after_delay_ms", 800)) / 1000.0
    base_out = args.out or cfg.get("output", {}).get("dir", "captures")
    if not os.path.isabs(base_out):
        base_out = os.path.join(HERE, base_out)

    suffix = "_dryrun" if args.dry_run else ""
    run_dir = os.path.join(base_out, datetime.now().strftime("%Y-%m-%d_%H%M%S") + suffix)
    os.makedirs(run_dir, exist_ok=True)
    setup_logging(run_dir, args.verbose)
    LOG.info("run folder: %s", run_dir)

    obs = ObsSession(host=obs_cfg.get("host", "localhost"),
                     port=int(obs_cfg.get("port", 4455)),
                     password=obs_cfg.get("password", ""),
                     timeout=float(obs_cfg.get("timeout", 5)))
    exit_code = EXIT_OK

    try:
        # 1. OBS, opened if it isn't already.
        obs.ensure_running(exe_path=obs_cfg.get("exe_path"),
                           wait_s=float(obs_cfg.get("launch_wait_s", 40)),
                           settle_s=float(obs_cfg.get("launch_settle_s", 3)))
        obs.connect()
        obs.check_format(img_format)
        obs.check_source(source)

        # 2. The two windows, and everything the panel will tell us about itself.
        game = winfocus.find_window(target_cfg.get("process", "HuffNPuffLink.exe"),
                                    target_cfg.get("window_class", "UnityWndClass"))
        LOG.info("game window: %s", game)
        # A minimised window gives OBS no frames to capture.
        winfocus.ensure_restored(game)

        panel = ideck.load_panel(ideck_cfg.get("layout"), ideck_cfg.get("actions"))
        deck_window = ideck.find_window(ideck_cfg.get("process", ideck.DEFAULT_PROCESS),
                                        ideck_cfg.get("window_class", ideck.DEFAULT_WINDOW_CLASS))
        LOG.info("i-Deck window: %s", deck_window)
        watcher = gamelog.GameLogWatcher(gamelog_cfg.get("path", gamelog.DEFAULT_LOG))
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
        size = capture_size(obs, scene, source, game)

        if args.dry_run:
            path = obs.screenshot(source, os.path.join(run_dir, f"dryrun.{img_format}"),
                                  *size, img_format)
            LOG.info("saved %s", path)
            LOG.info("dry run OK -- OBS, both windows, the panel layout, the game log and "
                     "screenshotting all work. It would press %s (position %d). Next: run it "
                     "without --dry-run", button.name, button.position)
            return EXIT_OK

        # 3. Before, spin, wait for the game to finish it, after.
        before = shot(obs, source, os.path.join(run_dir, f"before.{img_format}"),
                      size, img_format)

        # A win left uncollected -- by a previous run or by hand -- changes what our press does.
        carry = state.get("gamble") == "offerState"
        if carry:
            LOG.info("a win is pending, so this press collects it and bets again")

        started = time.monotonic()
        # Mark the log before pressing, or an event landing during the press is missed.
        watcher.mark()
        pressed_at = datetime.now().isoformat(timespec="milliseconds")
        ideck.press(panel, deck_window, button_spec,
                    hold_ms=int(ideck_cfg.get("click_hold_ms", 80)), watcher=press_log,
                    confirm_timeout=float(ideck_cfg.get("confirm_timeout_s", 2.0)))
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
        # The terminal event fires when the game decides the spin is over, while the last frame
        # of it is still being drawn. On a win the meter may still be counting up.
        time.sleep(after_delay)
        after = shot(obs, source, os.path.join(run_dir, f"after.{img_format}"),
                     size, img_format)
        measured = round(time.monotonic() - started, 3)

        summary = classify(events)
        record = {
            "run_dir": run_dir,
            "obs_version": obs.version,
            "game_window": repr(game),
            "ideck": ideck.describe(panel, deck_window, state),
            "game_log": watcher.path,
            "press_log": press_log.path,
            "capture_size": f"{size[0]}x{size[1]}",
            "button": {"name": button.name, "position": button.position,
                       "pressed_at": pressed_at},
            "collected_a_pending_win": carry,
            "before": before,
            "after": after,
            "measured_s": measured,
            "terminal_event": terminal,
            "idle_timeout_s": idle_timeout,
            "ceiling_s": ceiling,
            **summary,
            "events": events,
        }
        with open(os.path.join(run_dir, "spin.json"), "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)

        LOG.info("spin finished in %.2fs on %s: %s%s", measured, terminal or "a timeout",
                 summary["outcome"],
                 f", stops {summary['final_stops']}" if summary["final_stops"] else "")

    except KeyboardInterrupt:
        LOG.warning("interrupted")
        exit_code = EXIT_ABORTED
    except (ObsError, winfocus.WindowNotFound, ideck.IdeckError, gamelog.GameLogError,
            RuntimeError) as exc:
        LOG.error("error: %s", exc)
        exit_code = EXIT_ERROR
    except Exception:
        LOG.exception("unexpected failure")
        exit_code = EXIT_ERROR
    finally:
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
