"""The spin-capture loop, shared by spin_ideck.py and spin_key.py.

Both scripts do exactly the same thing: launch OBS if it isn't up, then for each spin take a
screenshot, trigger the spin, wait for the game to finish it, and screenshot each interesting
moment. They differ only in *how* the spin is triggered, which is the `spinner` passed to run().

A spinner needs three things:

    .trigger   short name for the log, e.g. "ideck"
    .control   what it presses, e.g. "Repeat Bet -> Rebet (position 12)"
    .prepare() once before the loop; may steal focus
    .spin()    trigger one spin; raise if it demonstrably didn't happen

The wait is driven by the game's own log (see gamelog.py), not by a sleep: a spin ends when the
game says `game_over`, so an ordinary 3 s spin isn't padded out and a 53 s feature isn't cut off.
`--delay` is the ceiling on that wait. With --no-gamelog it goes back to being a flat sleep.
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
import telemetry
import winfocus
from obs_client import ObsError, ObsSession

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = logging.getLogger("spin")

EXIT_OK, EXIT_ERROR, EXIT_ABORTED = 0, 1, 2

# A game window smaller than this in either axis isn't finished starting, or is minimised.
MIN_CLIENT_DIM = 100


# -- setup -----------------------------------------------------------------


def build_parser(description: str) -> argparse.ArgumentParser:
    """The options both scripts share. Each adds its own trigger-specific ones."""
    parser = argparse.ArgumentParser(description=description,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", "--spins", type=int, help="how many spins to run")
    parser.add_argument("--delay", type=float,
                       help="ceiling in seconds on the wait for the game to finish a spin "
                            "(with --no-gamelog, a flat sleep instead)")
    parser.add_argument("--out", help="base folder that run folders are created in")
    parser.add_argument("--config", default=os.path.join(HERE, "config.json"))

    watching = parser.add_mutually_exclusive_group()
    watching.add_argument("--gamelog", dest="gamelog", action="store_true", default=True,
                         help="wait on the game's own events, and shoot each one (default)")
    watching.add_argument("--no-gamelog", dest="gamelog", action="store_false",
                         help="don't read the game log; just sleep for --delay")

    parser.add_argument("--idle-timeout", type=float,
                       help="a spin is over when the game logs nothing for this long "
                            "(restarts on every event, so features are followed to the end)")

    recording = parser.add_mutually_exclusive_group()
    recording.add_argument("--record", dest="record", action="store_true", default=True,
                          help="start and stop OBS recording around the run (default)")
    recording.add_argument("--no-record", dest="record", action="store_false",
                          help="don't touch OBS recording; you control it")

    launching = parser.add_mutually_exclusive_group()
    launching.add_argument("--launch", dest="launch", action="store_true", default=True,
                          help="start OBS if it isn't already running (default)")
    launching.add_argument("--no-launch", dest="launch", action="store_false",
                          help="fail instead of starting OBS")

    parser.add_argument("--dry-run", action="store_true",
                       help="check everything and save one screenshot, without spinning")
    parser.add_argument("--list-windows", action="store_true",
                       help="print visible windows (to find an exe or window class) and exit")
    parser.add_argument("-v", "--verbose", action="store_true",
                       help="debug logging (always written to run.log regardless)")
    return parser


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    password = os.environ.get("OBS_WS_PASSWORD")
    if password:
        cfg.setdefault("obs", {})["password"] = password
    return cfg


def pick(cli_value, section: dict, key: str, default=None):
    """CLI flag wins over config.json, which wins over the built-in default."""
    return cli_value if cli_value is not None else section.get(key, default)


class _ScrubSecrets(logging.Filter):
    """Drop the obsws-python line that logs the OBS password in plaintext.

    On connect the SDK logs "Connecting with parameters: ... password='...'" at INFO. That
    must not end up in run.log.
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
    floor and yields a useless PNG while everything claims success. So wait for a size that
    is plausible against the game window's own client area.
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
        LOG.warning("WARNING: OBS never reported a usable size for %r (last: %s); falling "
                    "back to the game window's own %dx%d", source, last, client_w, client_h)
        return client_w, client_h
    raise RuntimeError(f"the game window reports a {client_w}x{client_h} client area -- it "
                       "looks minimised or still starting, so OBS has nothing to capture")


def _write_json(path: str, record: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)


def stamp(obs: ObsSession) -> dict:
    """When something happened: wall clock, plus the position in the OBS recording."""
    return {"wall_clock": datetime.now().isoformat(timespec="milliseconds"),
            "rec_timecode": obs.timecode()}


def shot(obs: ObsSession, source: str, path: str, size, img_format: str) -> dict:
    # Stamped just before the request, which is as close as we can get to the frame OBS grabs.
    entry = stamp(obs)
    obs.screenshot(source, path, *size, img_format)
    entry["file"] = os.path.basename(path)
    entry["bytes"] = os.path.getsize(path)
    return entry


def _spin_events(watcher, spin_dir, obs, source, size, img_format, capture_on, settle_s: float,
                 idle_timeout: float, ceiling: float, carry: bool) -> tuple[list, bool, bool]:
    """Follow one spin through the game log, screenshotting the moments worth having.

    Returns the events seen, whether the game reported this spin's outcome, and whether it left
    a win pending. A spin can emit the same event more than once -- a Hold & Spin feature
    produced eight reel stops, one per free spin -- so repeats get numbered rather than
    overwriting each other.

    `carry` says the previous spin ended on a pending win. Its `game_over` only arrives once
    this spin's press collects it, so the first one seen belongs to the previous spin and must
    not end this one. Without that, every win was followed by an empty ~1 s iteration and the
    reels of that spin were credited to the one after it.
    """
    seen, counts, finished, pending = [], {}, False, False
    for event in watcher.drain(idle_timeout, ceiling):
        record = {"event": event.name,
                  "game_clock": event.at.strftime("%H:%M:%S.%f")[:-3] if event.at else None}

        if event.name == "game_over" and carry:
            carry = False
            record["belongs_to"] = "previous spin"
            seen.append(record)
            continue

        record.update(event.fields)
        if event.name in capture_on:
            counts[event.name] = counts.get(event.name, 0) + 1
            nth = counts[event.name]
            stem = event.name if nth == 1 else f"{event.name}_{nth:02d}"
            # The event fires when the game *decides* the reels are down; the last frame of
            # reel-stop easing is still being drawn, so give it a moment before shooting.
            if settle_s:
                time.sleep(settle_s)
            record.update(shot(obs, source, os.path.join(spin_dir, f"{stem}.{img_format}"),
                                size, img_format))

        seen.append(record)
        if event.name in gamelog.TERMINAL:
            finished = True
            pending = event.name == "win"
            break
    return seen, finished, pending


# -- the run ---------------------------------------------------------------


def run(args, make_spinner) -> int:
    """Drive the whole run. `make_spinner(cfg, game)` builds the trigger."""
    if args.list_windows:
        for window in winfocus.list_windows():
            print(f"{window.process:<28} {window.window_class:<22} "
                  f"{window.width}x{window.height:<6} {window.title}")
        return EXIT_OK

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

    spins = int(pick(args.spins, spin_cfg, "spins", 5))
    delay_s = float(pick(args.delay, spin_cfg, "delay_s", 180.0))
    idle_timeout = float(pick(args.idle_timeout, gamelog_cfg, "idle_timeout_s", 8.0))
    capture_on = set(gamelog_cfg.get("capture_on", ["reels_stopped"]))
    settle_s = float(gamelog_cfg.get("post_event_delay_ms", 250)) / 1000.0
    scene = capture_cfg.get("scene", "Scene")
    source = capture_cfg.get("source", "Window Capture")
    img_format = capture_cfg.get("format", "png")
    base_out = pick(args.out, cfg.get("output", {}), "dir", "captures")
    if not os.path.isabs(base_out):
        base_out = os.path.join(HERE, base_out)

    if spins < 1:
        print("error: --spins must be at least 1", file=sys.stderr)
        return EXIT_ERROR

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
    completed = 0

    try:
        if args.launch:
            obs.ensure_running(exe_path=obs_cfg.get("exe_path"),
                               wait_s=float(obs_cfg.get("launch_wait_s", 40)),
                               settle_s=float(obs_cfg.get("launch_settle_s", 3)))
        obs.connect()
        obs.check_format(img_format)
        obs.check_source(source)

        # Find the game before touching the recording -- no point recording a session for a
        # game that isn't running.
        game = winfocus.find_window(target_cfg.get("process", "HuffNPuffLink.exe"),
                                    target_cfg.get("window_class", "UnityWndClass"))
        LOG.info("game window: %s", game)
        # A minimised window gives OBS no frames to capture.
        winfocus.ensure_restored(game)

        spinner = make_spinner(cfg, game)
        LOG.info("spin trigger: %s", spinner.control)
        warning = spinner.elevation_warning()
        if warning:
            LOG.warning("WARNING: %s", warning)

        watcher = None
        ledger = None
        if args.gamelog:
            watcher = gamelog.GameLogWatcher(gamelog_cfg.get("path", gamelog.DEFAULT_LOG))
            LOG.info("game log: %s", watcher.path)
            LOG.info("shooting on: %s", ", ".join(sorted(capture_on)))
            # The game log says whether a spin won; only the platform ledger says how much.
            try:
                ledger = telemetry.Ledger(
                    cfg.get("telemetry", {}).get("root", telemetry.DEFAULT_ROOT))
                LOG.info("spin ledger: %s", ledger.path)
            except telemetry.TelemetryError as exc:
                LOG.warning("WARNING: no win amounts in spin.json -- %s", exc)

        if args.dry_run:
            LOG.info("dry run: nothing will be pressed and recording is untouched")
            if watcher:
                state = gamelog.current_state(watcher.path)
                LOG.info("game state: %s", state["deck"])
            size = capture_size(obs, scene, source, game)
            path = obs.screenshot(source, os.path.join(run_dir, f"dryrun.{img_format}"),
                                  *size, img_format)
            LOG.info("saved %s", path)
            LOG.info("dry run OK -- OBS, the game window, the trigger%s and screenshotting all "
                     "work. Next: run it without --dry-run",
                     ", the game log" if watcher else "")
            return EXIT_OK

        if args.record:
            obs.start_recording()
        spinner.prepare()
        size = capture_size(obs, scene, source, game)
        LOG.info("%d spin(s) via %s, %s", spins, spinner.control,
                 f"each ends when the game reports its outcome ({idle_timeout:.0f}s quiet or "
                 f"{delay_s:.0f}s absolute at the outside)" if watcher
                 else f"{delay_s:.1f}s fixed settle each")

        # A winning spin parks the game on the collect offer, so both its game_over and its ledger
        # record land during the next spin. Carry both across iterations.
        #
        # Seed this from the game's current state, not from False: a previous run (or hand play)
        # can leave a win sitting uncollected, in which case the first press of *this* run collects
        # it rather than spinning, and its game_over belongs to that older spin. Without seeding,
        # spin_01 ended on the leftover event after 2.1 s -- too fast to have spun at all.
        win_pending = False
        if watcher:
            win_pending = gamelog.current_state(watcher.path).get("gamble") == "offerState"
            if win_pending:
                LOG.info("a win is already pending -- the first press collects it, then bets again")
        pending = None

        for index in range(1, spins + 1):
            spin_dir = os.path.join(run_dir, f"spin_{index:02d}")
            os.makedirs(spin_dir, exist_ok=True)

            before = shot(obs, source, os.path.join(spin_dir, f"before.{img_format}"),
                           size, img_format)
            started = time.monotonic()
            # Mark the log before pressing, or an event that lands during the press is missed.
            if watcher:
                watcher.mark()
            pressed = stamp(obs)
            spinner.spin()

            events, finished = [], None
            if ledger:
                # Mark now so the poll after the spin picks up only this spin's record.
                ledger.mark()
            if watcher:
                events, finished, win_pending = _spin_events(
                    watcher, spin_dir, obs, source, size, img_format, capture_on, settle_s,
                    idle_timeout, delay_s, carry=win_pending)
                if not finished:
                    LOG.warning("WARNING: spin %d: the game went quiet for %.0fs without "
                                "reporting an outcome. Shooting anyway; the frame may be "
                                "mid-animation.", index, idle_timeout)
            else:
                time.sleep(delay_s)

            after = shot(obs, source, os.path.join(spin_dir, f"after.{img_format}"),
                          size, img_format)

            record = {
                "spin": index,
                "trigger": spinner.trigger,
                "control": spinner.control,
                "timeout_s": delay_s,
                "capture_size": f"{size[0]}x{size[1]}",
                "before": before,
                "pressed": pressed,
                "after": after,
                "measured_s": round(time.monotonic() - started, 3),
            }
            if watcher:
                record["finished"] = finished
                record["won"] = win_pending
                record["stops"] = next((e.get("stops") for e in reversed(events)
                                        if e.get("stops")), None)
                record["events"] = events
            if ledger:
                outcomes = ledger.poll()
                # The ledger record is written at end of game, which for a *winning* spin only
                # happens once the next press collects it -- so it arrives one spin late. Hand the
                # oldest record to the spin that is still waiting, oldest first, and rewrite that
                # spin's file. Without this the winning spin lost its amount entirely, which is
                # the one number most worth having.
                if pending and outcomes:
                    pending_path, pending_record = pending
                    pending_record["ledger"] = outcomes.pop(0)
                    _write_json(pending_path, pending_record)
                    LOG.info("       spin %d paid %.2f (ledger arrived late, as it does on a win)",
                             pending_record["spin"],
                             pending_record["ledger"].get("amount_won") or 0)
                    pending = None
                record["ledger"] = outcomes[-1] if outcomes else None
            spin_json = os.path.join(spin_dir, "spin.json")
            _write_json(spin_json, record)
            if ledger and record.get("ledger") is None:
                pending = (spin_json, record)
            completed = index

            shots = [e["event"] for e in events if "file" in e]
            paid = (record.get("ledger") or {}).get("amount_won")
            LOG.info("[%d/%d] %s: %.2fs%s%s%s%s", index, spins, os.path.basename(spin_dir),
                     record["measured_s"],
                     f", stops {record['stops']}" if record.get("stops") else "",
                     " WON" if record.get("won") else "",
                     f" (paid {paid:.2f})" if paid else "",
                     f", shot {', '.join(shots)}" if shots else "")

        # If the run ended on a win, that spin's ledger record is still in flight. Wait briefly
        # for it rather than leaving the last spin without its amount.
        if ledger and pending:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                outcomes = ledger.poll()
                if outcomes:
                    pending[1]["ledger"] = outcomes[0]
                    _write_json(pending[0], pending[1])
                    LOG.info("spin %d paid %.2f", pending[1]["spin"],
                             outcomes[0].get("amount_won") or 0)
                    break
                time.sleep(0.25)
            else:
                LOG.info("spin %d has no ledger record yet -- it lands when the win is collected",
                         pending[1]["spin"])

    except KeyboardInterrupt:
        LOG.warning("interrupted after %d spin(s)", completed)
        exit_code = EXIT_ABORTED
    except (ObsError, winfocus.WindowNotFound, gamelog.GameLogError, RuntimeError) as exc:
        LOG.error("error: %s", exc)
        exit_code = EXIT_ERROR
    except Exception:
        LOG.exception("unexpected failure")
        exit_code = EXIT_ERROR
    finally:
        # Always stop a recording we started, even on Ctrl+C, or OBS keeps recording after
        # the script is gone.
        try:
            video = obs.stop_recording()
            if video:
                LOG.info("video: %s", video)
        except Exception:
            LOG.debug("stop_recording failed during cleanup", exc_info=True)
        obs.close()
        LOG.info("done: %d spin(s), output in %s", completed, run_dir)
        prune_empty(run_dir)

    return exit_code
