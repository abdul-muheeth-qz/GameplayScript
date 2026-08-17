"""The run folder as the server sees it: name it, fill it, read it back.

One folder per run holds every stage's output, and the run id is its name. That is the
whole state model -- the server keeps nothing in memory between requests, so a reload,
a restart or a second browser tab all see the same thing, and a run from last week
replays exactly like one from a minute ago.

    captured_files/<run_id>/
        pre_spin.png  spin_result.png  spin.json  run.log  spin.mp4   capture
        win_collected.png                                             capture, wins only
        extract/pre_spin.json  extract/spin_result.json  *_roi.png    extract
        validate.json                                                 validate
        payline/reels.png  tiles/  tiles.json  annotated_*.png        payline
        payline.json                                                  payline

A winning spin has three frames, because a win is not in the cash meter until it is
collected -- see `server.frames`, which owns those names.

The two audits are independent tenants of the same folder: the meter one reads all the
frames and adds the money up, the payline one reads `spin_result`'s pixels and walks the
lines. Either can be run without the other, in either order, and
`state` returns both so one page can show them side by side.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
from datetime import datetime

from . import frames as frame_names
from .settings import ROOT, captures_dir
from .extract.runner import extract_dir, read_frames
from .payline.runner import read_result as read_payline
from .payline.runner import read_tiles as read_payline_tiles
from .validate.runner import read_result

LOG = logging.getLogger("server")

# Only ever a timestamp we made ourselves, but it arrives back from the browser, so it is
# checked before it is joined onto a path.
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

# Only one spin at a time. Two overlapping runs would fight over the single OBS instance's
# record directory, over the physical cursor the i-Deck click parks, and -- before
# --run-dir existed -- over the same second-granularity folder name.
CAPTURE_LOCK = asyncio.Lock()

# spin.py's own exit codes.
EXIT_OK, EXIT_ERROR, EXIT_ABORTED = 0, 1, 2


class RunError(Exception):
    """Something went wrong in a way the user can act on."""


def new_run_id() -> str:
    """A fresh run id. Second-granularity like spin.py's own, plus a counter for the
    case that two runs start inside the same second -- which the folder name alone
    cannot distinguish, and which would silently merge two runs into one."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base = captures_dir({})
    candidate, n = stamp, 1
    while os.path.exists(os.path.join(base, candidate)):
        candidate = f"{stamp}-{n}"
        n += 1
    return candidate


def run_dir(cfg: dict, run_id: str) -> str:
    """The folder for `run_id`, having checked the id cannot escape captured_files/."""
    if not RUN_ID_RE.match(run_id):
        raise RunError(f"not a run id: {run_id!r}")
    return os.path.join(captures_dir(cfg), run_id)


def require_run(cfg: dict, run_id: str) -> str:
    path = run_dir(cfg, run_id)
    if not os.path.isdir(path):
        raise RunError(f"no run called {run_id} -- it may have been deleted, or the "
                       f"capture step may have failed before writing anything")
    return path


def artifact(cfg: dict, run_id: str, name: str) -> str:
    """An absolute path to one file inside a run folder, or raise.

    The name is resolved and then checked to be inside the folder, rather than merely
    scanned for "..", because that is the check that holds for symlinks and for the
    several spellings Windows accepts for the same path.
    """
    folder = require_run(cfg, run_id)
    path = os.path.realpath(os.path.join(folder, name))
    if os.path.commonpath([path, os.path.realpath(folder)]) != os.path.realpath(folder):
        raise RunError(f"{name!r} is not inside run {run_id}")
    if not os.path.isfile(path):
        raise RunError(f"{name!r} does not exist in run {run_id}")
    return path


# -- capture ---------------------------------------------------------------


async def capture(cfg: dict, run_id: str, *, dry_run=False, no_record=False,
                  config_path: str | None = None) -> dict:
    """Run one spin as a subprocess and return what spin.json says about it.

    A subprocess, not an import, for three separate reasons and any one would be enough:
    `spin.setup_logging` takes over the root logger and leaves a FileHandler open on the
    run folder, which on Windows then cannot be deleted; the i-Deck click only lands from
    a **DPI-unaware** process, and a server host that has made itself DPI-aware would
    silently send every click a column to the left; and a spin can take three minutes,
    which has no business happening inside the event loop.
    """
    folder = run_dir(cfg, run_id)
    os.makedirs(folder, exist_ok=True)

    argv = [sys.executable, "-m", "server.capture.spin", "--run-dir", folder]
    if dry_run:
        argv.append("--dry-run")
    if no_record:
        argv.append("--no-record")
    if config_path:
        argv += ["--config", config_path]

    LOG.info("capture: %s", " ".join(argv))
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        # spin.py never reads stdin, and a console handle it does not need is one more
        # thing that can differ between running it by hand and running it from here.
        stdin=subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode("utf-8", errors="replace")
    for line in output.splitlines():
        LOG.info("  spin | %s", line)

    if proc.returncode != EXIT_OK:
        # spin.py already wrote the actionable message; the last few lines carry it.
        tail = "\n".join(line for line in output.strip().splitlines()[-8:])
        reason = {EXIT_ABORTED: "the capture was interrupted"}.get(
            proc.returncode, "the capture failed")
        raise RunError(f"{reason} (exit {proc.returncode}).\n{tail}")

    record = read_spin(cfg, run_id)
    if record is None:
        # prune_empty deletes a folder that captured nothing at all.
        raise RunError("the capture reported success but wrote no spin.json -- check "
                       "run.log in the run's folder under captured_files/")
    return record


def read_spin(cfg: dict, run_id: str) -> dict | None:
    """spin.json for this run, or None if the capture step hasn't run or left nothing."""
    import json

    path = os.path.join(run_dir(cfg, run_id), "spin.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


# -- assembling what the UI shows ------------------------------------------


def frames(cfg: dict, run_id: str) -> dict:
    """{"pre_spin": "pre_spin.png", ...} for whichever frames this run captured.

    Two on a losing spin and three on a winning one, so the UI must render whatever is here
    rather than expecting a fixed pair.
    """
    folder = run_dir(cfg, run_id)
    found = {}
    for name in frame_names.ORDER:
        path = frame_names.find(folder, name)
        if path:
            found[name] = os.path.basename(path)
    return found


def crops(cfg: dict, run_id: str) -> dict:
    """The ROI crop each frame was read from, as run-folder-relative paths."""
    folder = run_dir(cfg, run_id)
    found = {}
    for name in frame_names.ORDER:
        frame = frame_names.find(folder, name)
        if not frame:
            continue
        stem = os.path.splitext(os.path.basename(frame))[0]
        crop = os.path.join(extract_dir(folder), f"{stem}_roi.png")
        if os.path.isfile(crop):
            found[name] = f"extract/{stem}_roi.png"
    return found


def summarise(spin: dict | None) -> dict | None:
    """The handful of spin.json fields worth putting on screen."""
    if not spin:
        return None
    return {
        "outcome": spin.get("outcome"),
        "won": spin.get("won"),
        "measured_s": spin.get("measured_s"),
        "terminal_event": spin.get("terminal_event"),
        "button": (spin.get("button") or {}).get("name"),
        "capture_size": spin.get("capture_size"),
        "collected_a_pending_win": spin.get("collected_a_pending_win"),
        # Whether the win at the end of this spin was taken on the glass, which is what makes
        # the third frame exist and the spin's cash meter final.
        "win_collected": bool(spin.get("win_collect")),
        "final_stops": spin.get("final_stops"),
        "video": (spin.get("video") or {}).get("file"),
        "event_count": len(spin.get("events") or []),
    }


def state(cfg: dict, run_id: str) -> dict:
    """Everything known about a run, so a page refresh can rebuild itself.

    Both audits are in here, and neither depends on the other: a run may hold a meter
    verdict, a payline verdict, both or neither, and the two pages read the same object.
    """
    require_run(cfg, run_id)
    spin = read_spin(cfg, run_id)
    folder = run_dir(cfg, run_id)
    return {
        "run_id": run_id,
        "frames": frames(cfg, run_id),
        "spin": summarise(spin),
        "extraction": read_frames(folder),
        "crops": crops(cfg, run_id),
        "validation": read_result(folder),
        "payline_tiles": read_payline_tiles(folder),
        "payline": read_payline(folder),
    }


def all_runs(cfg: dict) -> list[str]:
    """Every run id, newest first. Ids are timestamps, so that is a reverse name sort."""
    base = captures_dir(cfg)
    if not os.path.isdir(base):
        return []
    return sorted((name for name in os.listdir(base)
                   if os.path.isdir(os.path.join(base, name)) and RUN_ID_RE.match(name)),
                  reverse=True)


def recent(cfg: dict, limit: int = 20) -> list[str]:
    """The most recent run ids, newest first."""
    return all_runs(cfg)[:limit]


def latest(cfg: dict, frame: str | None = None) -> str | None:
    """The newest run id, or the newest one that actually captured `frame`.

    The `frame` filter is what makes this useful to a stage rather than to a listing: the
    newest folder is not necessarily the newest *usable* one. A capture that failed early
    leaves a folder holding a run.log and nothing else -- four of the folders on this machine
    have no spin.json at all -- so a stage that took the top of the list would offer a run and
    then fail on it. Asking for the newest run holding a `spin_result` skips those instead.
    """
    for run_id in all_runs(cfg):
        if frame is None or frame_names.find(run_dir(cfg, run_id), frame):
            return run_id
    return None
