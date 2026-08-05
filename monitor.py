#!/usr/bin/env python
"""Watch the game and screenshot every event, without driving it.

The two spin scripts press a button and capture the spin they caused. This one presses nothing:
it follows whatever happens -- including what you do by hand -- and takes a screenshot the moment
the game logs each event. Changing the denomination, changing the bet, a feature starting, a win
appearing, and whether you then chose Gamble or Take Win all land as their own frame.

    python monitor.py                    # watch until Ctrl+C
    python monitor.py --spins 20         # stop after 20 spins
    python monitor.py --duration 600     # stop after 10 minutes
    python monitor.py --capture-all      # every event, not the curated set

Output is one folder per session: numbered PNGs in the order things happened, and
`timeline.jsonl` -- one JSON object per line, appended as each event lands, so a session
interrupted with Ctrl+C keeps everything up to that point.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime

import gamelog
import runner
import telemetry
import winfocus
from obs_client import ObsError, ObsSession

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = logging.getLogger("spin.monitor")

# Worth a frame each. Left out of the default because they fire constantly and would bury the
# session in near-identical images: deck_changed (2581 in one log), cash_symbol, idle_state,
# gamble_state, new_game_allowed, reels_spinning, final_grid. --capture-all includes them.
DEFAULT_CAPTURE = [
    "denom_changed", "bet_changed",
    "spin_started", "reels_stopped",
    "mystery_reveal",
    "feature_triggered", "hold_and_spin_prompt", "hold_and_spin_started",
    "wager_saver_offered", "wager_saver_accepted",
    "win", "take_win", "gamble_played",
    "jackpot_awarded", "jackpot_celebration", "progressive_level",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", "--spins", type=int,
                       help="stop after this many spins (default: until Ctrl+C)")
    parser.add_argument("--duration", type=float, metavar="SECONDS",
                       help="stop after this long (default: until Ctrl+C)")
    parser.add_argument("--capture-all", action="store_true",
                       help="screenshot every event, including the very frequent ones")
    parser.add_argument("--out", help="base folder that session folders are created in")
    parser.add_argument("--config", default=os.path.join(HERE, "config.json"))

    recording = parser.add_mutually_exclusive_group()
    recording.add_argument("--record", dest="record", action="store_true", default=True,
                          help="start and stop OBS recording around the session (default)")
    recording.add_argument("--no-record", dest="record", action="store_false",
                          help="don't touch OBS recording; you control it")

    launching = parser.add_mutually_exclusive_group()
    launching.add_argument("--launch", dest="launch", action="store_true", default=True,
                          help="start OBS if it isn't already running (default)")
    launching.add_argument("--no-launch", dest="launch", action="store_false",
                          help="fail instead of starting OBS")

    parser.add_argument("--no-telemetry", dest="telemetry", action="store_false", default=True,
                       help="don't read the platform ledger (so no win amounts)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def _describe(event, fields: dict) -> str:
    """A one-line human reading of an event, for the console and run.log."""
    if event == "denom_changed":
        return f"denomination changed to {fields.get('denom')}"
    if event == "bet_changed":
        return f"bet changed ({fields.get('changed')})"
    if event == "bet_locked":
        return f"bet locked at {fields.get('total_bet')} (denom {fields.get('denom')})"
    if event == "reels_stopped":
        return f"reels stopped at {fields.get('stops')}"
    if event == "feature_triggered":
        return f"feature started: {fields.get('feature')}"
    if event == "progressive_level":
        return f"progressive level {fields.get('level')}"
    return {
        "spin_started": "spin started",
        "mystery_reveal": "mystery symbol revealed",
        "hold_and_spin_prompt": "Hold & Spin waiting to be started",
        "hold_and_spin_started": "Hold & Spin started",
        "wager_saver_offered": "wager saver offered",
        "wager_saver_accepted": "wager saver accepted",
        "win": "WIN -- collect/gamble offered, the deck now reads Collect Win",
        "take_win": "chose TAKE WIN",
        "gamble_played": "chose GAMBLE",
        "jackpot_awarded": "JACKPOT awarded",
        "jackpot_celebration": "jackpot celebration",
        "game_over": "spin complete",
    }.get(event, event)


def run(args) -> int:
    try:
        cfg = runner.load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read config {args.config}: {exc}", file=sys.stderr)
        return runner.EXIT_ERROR

    obs_cfg = cfg.get("obs", {})
    capture_cfg = cfg.get("capture", {})
    target_cfg = cfg.get("target", {})
    gamelog_cfg = cfg.get("gamelog", {})
    telemetry_cfg = cfg.get("telemetry", {})

    scene = capture_cfg.get("scene", "Scene")
    source = capture_cfg.get("source", "Window Capture")
    img_format = capture_cfg.get("format", "png")
    settle_s = float(gamelog_cfg.get("post_event_delay_ms", 250)) / 1000.0
    base_out = runner.pick(args.out, cfg.get("output", {}), "dir", "captures")
    if not os.path.isabs(base_out):
        base_out = os.path.join(HERE, base_out)

    capture_on = None if args.capture_all else set(
        gamelog_cfg.get("monitor_capture_on", DEFAULT_CAPTURE))

    session_dir = os.path.join(base_out, "monitor_" + datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    os.makedirs(session_dir, exist_ok=True)
    runner.setup_logging(session_dir, args.verbose)
    LOG.info("session folder: %s", session_dir)

    obs = ObsSession(host=obs_cfg.get("host", "localhost"),
                     port=int(obs_cfg.get("port", 4455)),
                     password=obs_cfg.get("password", ""),
                     timeout=float(obs_cfg.get("timeout", 5)))
    exit_code = runner.EXIT_OK
    shots = spins = 0
    timeline = os.path.join(session_dir, "timeline.jsonl")

    try:
        if args.launch:
            obs.ensure_running(exe_path=obs_cfg.get("exe_path"),
                               wait_s=float(obs_cfg.get("launch_wait_s", 40)),
                               settle_s=float(obs_cfg.get("launch_settle_s", 3)))
        obs.connect()
        obs.check_format(img_format)
        obs.check_source(source)

        game = winfocus.find_window(target_cfg.get("process", "HuffNPuffLink.exe"),
                                    target_cfg.get("window_class", "UnityWndClass"))
        LOG.info("game window: %s", game)
        winfocus.ensure_restored(game)

        watcher = gamelog.GameLogWatcher(gamelog_cfg.get("path", gamelog.DEFAULT_LOG))
        LOG.info("game log: %s", watcher.path)

        ledger = None
        if args.telemetry:
            try:
                ledger = telemetry.Ledger(telemetry_cfg.get("root", telemetry.DEFAULT_ROOT))
                ledger.mark()
                LOG.info("spin ledger: %s", ledger.path)
            except telemetry.TelemetryError as exc:
                LOG.warning("WARNING: no win amounts -- %s", exc)

        if args.record:
            obs.start_recording()
        size = runner.capture_size(obs, scene, source, game)

        LOG.info("shooting on: %s", "every event" if capture_on is None
                 else ", ".join(sorted(capture_on)))
        LOG.info("watching -- play the game; Ctrl+C to stop")
        if args.capture_all:
            LOG.warning("WARNING: --capture-all shoots frequent events too (deck_changed alone "
                        "fired 2581 times in one log); expect a lot of near-identical frames")

        started = time.monotonic()
        # A generous idle timeout and no ceiling: an idle machine is the normal state here, so
        # this drains for as long as the session lasts rather than deciding the game is finished.
        for event in watcher.drain(idle_timeout=float("inf"), ceiling=float("inf")):
            if event.name == "spin_started":
                spins += 1

            record = {
                "seq": None,
                "event": event.name,
                "spin": spins,
                "game_clock": event.at.strftime("%H:%M:%S.%f")[:-3] if event.at else None,
                "note": _describe(event.name, event.fields),
            }
            record.update(event.fields)

            if capture_on is None or event.name in capture_on:
                shots += 1
                record["seq"] = shots
                if settle_s:
                    time.sleep(settle_s)
                name = f"{shots:04d}_{event.name}.{img_format}"
                record.update(runner.shot(obs, source, os.path.join(session_dir, name),
                                          size, img_format))
                LOG.info("[%04d] spin %d  %s", shots, spins, record["note"])
            else:
                record.update(runner.stamp(obs))
                LOG.debug("       spin %d  %s", spins, record["note"])

            with open(timeline, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")

            # The ledger entry for a spin is written when the spin ends, so it lands here rather
            # than with the event that caused it. It is the only source for the amount won.
            if ledger and event.name in ("game_over", "win"):
                for outcome in ledger.poll():
                    won = outcome.get("amount_won") or 0
                    LOG.info("       spin %d  ledger: bet %.2f, %s, credit %.2f", spins,
                             outcome.get("total_bet") or 0,
                             f"WON {won:.2f}" if won else "no win",
                             outcome.get("credit_after") or 0)
                    with open(timeline, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"event": "ledger", "spin": spins,
                                             **outcome}) + "\n")

            if args.spins and spins >= args.spins and event.name in ("game_over", "win"):
                LOG.info("reached %d spin(s)", spins)
                break
            if args.duration and time.monotonic() - started >= args.duration:
                LOG.info("reached %.0fs", args.duration)
                break

    except KeyboardInterrupt:
        LOG.warning("stopped after %d event screenshot(s) over %d spin(s)", shots, spins)
        exit_code = runner.EXIT_ABORTED
    except (ObsError, winfocus.WindowNotFound, gamelog.GameLogError, RuntimeError) as exc:
        LOG.error("error: %s", exc)
        exit_code = runner.EXIT_ERROR
    except Exception:
        LOG.exception("unexpected failure")
        exit_code = runner.EXIT_ERROR
    finally:
        try:
            video = obs.stop_recording()
            if video:
                LOG.info("video: %s", video)
        except Exception:
            LOG.debug("stop_recording failed during cleanup", exc_info=True)
        obs.close()
        LOG.info("done: %d screenshot(s), %d spin(s), output in %s", shots, spins, session_dir)
        runner.prune_empty(session_dir)

    return exit_code


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
