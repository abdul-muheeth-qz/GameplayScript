"""Where the reel stops come from: the game's own log, the same file `gamelog.py` reads.

    08/06/26 00:07:29.861 00 HuffNPuffLink:5760 INF:
        [Slot.HandleSlotReelStoppedMessage] reelsStops[107 93 66 94 95]

One stop per reel, left to right; `reelstrips.py` turns those five numbers into fifteen names. This
replaced the platform's telemetry service, and the swap was measured first: over every entry both
sources hold here, 593 agree and 0 disagree, the game line landing 0.51 s later, with two extra
spins the telemetry missed.

Three things are load-bearing. **The marker is anchored on its handler**, not on the word
`reelsStops` -- the same log carries `LastStopsMsg`, `StopsMsg` and `SyncStopsMsg`. **The rotated
siblings are merged**, because the log rotates at ~20 MB and reading only the live path would stand
the checkpoint down on any run older than the last rotation. And **which entry decides whether the
checkpoint is even about the right spin**: the stops line lands a few seconds before the frame it
belongs to, so the default is the last entry at or before the frame's own timestamp, and falling
back to the last entry in the file is *reported* (`matched_by`), never silent.

Not every game writes it -- FortuneOx logs it to a second file this does not point at -- and that is
reported as `unavailable` rather than worked around.
"""

from __future__ import annotations

import datetime as dt
import glob
import logging
import os
import re

from ..settings import resolve

LOG = logging.getLogger("payline")

# How far before the frame an entry may sit and still be that frame's spin. The measured gap is a
# few seconds; 15 minutes is loose on purpose -- it rejects *another session's* stops rather than
# policing seconds, and a Hold & Spin can put a minute between the stop and the frame.
DEFAULT_TOLERANCE_S = 900

# Named once, so the error messages cannot drift from the pattern.
MARKER = "[Slot.HandleSlotReelStoppedMessage] reelsStops[...]"

STOPS_RE = re.compile(r'HandleSlotReelStoppedMessage\]\s*reelsStops\[([0-9\s]+)\]')
TIMESTAMP_RE = re.compile(r'^(\d\d/\d\d/\d\d \d\d:\d\d:\d\d\.\d+)')
TIMESTAMP_FMT = "%m/%d/%y %H:%M:%S.%f"


class TelemetryError(Exception):
    """The reel stops could not be found, in a way the user can act on."""


class Entry:
    """One reel-stops line: the stops, and enough to say which spin it was."""

    def __init__(self, stops, timestamp, path, line_no):
        self.stops = stops
        self.timestamp = timestamp          # naive local datetime, or None if unparsable
        self.path = path
        self.line_no = line_no

    def describe(self) -> dict:
        return {
            "stops": self.stops,
            "timestamp": self.timestamp.isoformat(sep=" ") if self.timestamp else None,
            "file": os.path.basename(self.path),
            "line": self.line_no,
        }


def game_logs(cfg: dict) -> list[str]:
    """The active game's log and its rotated siblings, oldest first.

    One name in one place, `games.<exe>.log` -- deliberately no payline-level override, since a
    second setting naming the same file is what `telemetry_dir` was.

    Ordered by **name**, not mtime: the rotated names carry a sortable timestamp and sort before the
    live log, while mtime lies because the game holds the handle open. Only a stable pre-order
    anyway; `collect_entries` sorts on each line's own clock.
    """
    game = cfg.get("game") or {}
    configured = game.get("log")
    if not configured:
        raise TelemetryError(
            "the active game has no \"log\" in game_config.json, so there is nowhere to read "
            "this spin's reel stops from. Set games.<exe>.log to the file the game writes, or "
            "set payline.reel_stops.enabled to false to audit on the pixels alone")

    live = resolve(configured)
    folder, name = os.path.split(live)
    stem, ext = os.path.splitext(name)
    rotated = glob.glob(os.path.join(glob.escape(folder),
                                     glob.escape(stem) + "-*" + glob.escape(ext)))
    paths = sorted({live, *rotated})
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        raise TelemetryError(
            f"the game log {live} does not exist, so this spin's reel stops cannot be read. "
            f"Check games.<exe>.log in game_config.json, or set payline.reel_stops.enabled to "
            f"false to audit on the pixels alone")
    return paths


def _timestamp(text: str) -> dt.datetime | None:
    """`08/06/26 00:07:29.861` -> a naive local datetime. Month first, and everything it is
    compared against (a frame's mtime) is local time too."""
    try:
        return dt.datetime.strptime(text, TIMESTAMP_FMT)
    except (ValueError, TypeError):
        return None


def read_entries(path: str) -> list[Entry]:
    """Every reel-stops line in one file, in the order they were written."""
    entries = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, start=1):
            found = STOPS_RE.search(line)
            if not found:
                continue
            stops = [int(n) for n in found.group(1).split()]
            if not stops:
                continue
            stamp = TIMESTAMP_RE.match(line)
            entries.append(Entry(stops,
                                 _timestamp(stamp.group(1)) if stamp else None,
                                 path, line_no))
    return entries


def collect_entries(paths: list[str]) -> list[Entry]:
    """Every reel-stops line across the logs, oldest first.

    Merged rather than "the newest file that has any", because rotation puts an older run's stops in
    a sibling and `pick_entry` chooses on the frame's own time, not on the file.
    """
    entries: list[Entry] = []
    for path in paths:
        entries.extend(read_entries(path))
    if not entries:
        raise TelemetryError(
            f"none of the {len(paths)} game log(s) at "
            f"{os.path.dirname(paths[-1]) or '.'} contains a {MARKER} line. That marker is "
            f"written when the reels stop, so check that a spin has been played since the game "
            f"started, and that this game writes it to the log games.<exe>.log names -- "
            f"FortuneOx logs it to its *server* log, which that key does not point at")
    # By each line's own clock rather than by file, the mtime being unreliable while the game holds
    # the handle open. Undated lines sort first, so the `entries[-1]` fallback lands on a dated one.
    entries.sort(key=lambda e: e.timestamp or dt.datetime.min)
    return entries


def pick_entry(entries: list[Entry], frame_time: dt.datetime | None,
               tolerance_s: float = DEFAULT_TOLERANCE_S) -> tuple[Entry, str, bool]:
    """The entry for the frame being audited, how it was chosen, and whether that is *proof*.

    The last entry at or before `frame_time` is this frame's spin, and the third element is True
    only then. Otherwise the last entry comes back with False beside it and the caller must not
    decide a COMPARE on it -- one run on disk was captured at 11:42 against a log beginning at
    12:08, where the nearest entry was a spin four hours later.
    """
    if frame_time is not None:
        before = [e for e in entries
                  if e.timestamp is not None
                  and e.timestamp <= frame_time
                  and (frame_time - e.timestamp).total_seconds() <= tolerance_s]
        if before:
            best = max(before, key=lambda e: e.timestamp)
            gap = (frame_time - best.timestamp).total_seconds()
            return best, f"the frame's own time, {gap:.0f}s after these stops landed", True
        return entries[-1], (
            f"the last entry in the log -- no stops within {tolerance_s:.0f}s before the "
            f"frame, so this is a different spin"), False
    return entries[-1], "the last entry in the log (the frame has no timestamp to match)", False


def latest_stops(cfg: dict, frame_path: str | None = None,
                 tolerance_s: float = DEFAULT_TOLERANCE_S) -> dict:
    """The reel stops to audit against, plus everything needed to check that choice.

    Raises `TelemetryError` naming the config key when there is nothing to read; the caller decides
    whether that is fatal.
    """
    paths = game_logs(cfg)
    entries = collect_entries(paths)

    frame_time = None
    if frame_path and os.path.isfile(frame_path):
        frame_time = dt.datetime.fromtimestamp(os.path.getmtime(frame_path))

    entry, matched_by, matched = pick_entry(entries, frame_time, tolerance_s)
    record = entry.describe()
    record.update({
        "path": entry.path,
        "logs": len(paths),
        "entries": len(entries),
        "matched_by": matched_by,
        "matched": matched,
        "frame_time": frame_time.isoformat(sep=" ") if frame_time else None,
    })
    LOG.info("reel stops %s from %s (%s)", entry.stops, os.path.basename(entry.path), matched_by)
    return record
