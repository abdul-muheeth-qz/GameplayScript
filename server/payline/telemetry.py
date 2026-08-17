"""THE CHECKPOINT, part 2 -- where the reel stops come from.

The game writes one telemetry file per session under `C:\\logs\\Telemetry\\Data\\<game>`, and a
spin's landing positions appear in it as

    {"Timestamp_ISO8601":"2026-08-13T15:58:0905:30", "Game_Id":"65537;65047279698077",
     "GamePlay":{"BaseGameReelStops":["24","79","153","25","0"]}}

one stop per reel, left to right. `reelstrips.py` turns those five numbers into fifteen symbol
names.

**These lines are not valid JSON and must not be parsed as such.** Measured against the files
on this machine: `"Event":FortuneOx    [monitoring][GameMetrics]` has a bare word where a value
belongs, `ProgressiveQualified:False` is Python's spelling rather than JSON's, and the timestamp
reads `2026-08-13T15:58:0905:30` -- the `+` of the `+05:30` offset is missing, so even the date
does not parse. Hence regex for the three fields that matter and nothing else.

**The newest file is not necessarily the one with the stops.** The folder holds `_client_` and
`_server_` files, only the server ones carry `BaseGameReelStops` (36 entries across three server
files here, 0 across two client files), and the second-newest file by mtime is a client file. So
the search is "the newest file that actually contains an entry".

**Which entry, though, is the question that decides whether the checkpoint is even about the
right spin.** Taking the last one is right when you have just spun -- that is how the mapping in
the request was read off by hand -- and wrong the moment a run folder from earlier in the day is
re-audited, where it would confidently describe a different spin's reels. The timestamps make
that answerable: measured across six consecutive captures, the stops entry lands 3-4 s before
the `spin_result.png` it belongs to (15:51:03 → 15:51:07, 15:36:33 → 15:36:38, 15:16:37 →
15:16:40, 15:14:42 → 15:14:45, 15:11:59 → 15:12:02, 15:09:11 → 15:09:14). So the default is
**the last entry at or before the frame's own timestamp**, within `DEFAULT_TOLERANCE_S`, and
falling back to the last entry in the file is a *reported* fallback (`matched_by`), never a
silent one.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re

LOG = logging.getLogger("payline")

DEFAULT_DIR_ROOT = r"C:\logs\Telemetry\Data"

# How far before the frame an entry may sit and still be taken as that frame's spin. The
# measured gap is 3-4 s; 15 minutes is loose on purpose -- it is here to reject *another
# session's* stops, not to police seconds, and a Hold & Spin can put a minute between the
# stop landing and the frame being shot.
DEFAULT_TOLERANCE_S = 900

STOPS_RE = re.compile(r'"BaseGameReelStops"\s*:\s*\[([^\]]*)\]')
NUMBER_RE = re.compile(r'-?\d+')
TIMESTAMP_RE = re.compile(r'"Timestamp_ISO8601"\s*:\s*"([^"]+)"')
GAME_ID_RE = re.compile(r'"Game_Id"\s*:\s*"([^"]+)"')


class TelemetryError(Exception):
    """The reel stops could not be found, in a way the user can act on."""


class Entry:
    """One `BaseGameReelStops` line: the stops, and enough to say which spin it was."""

    def __init__(self, stops, timestamp, game_id, path, line_no):
        self.stops = stops
        self.timestamp = timestamp          # naive local datetime, or None if unparsable
        self.game_id = game_id
        self.path = path
        self.line_no = line_no

    def describe(self) -> dict:
        return {
            "stops": self.stops,
            "timestamp": self.timestamp.isoformat(sep=" ") if self.timestamp else None,
            "game_id": self.game_id,
            "file": os.path.basename(self.path),
            "line": self.line_no,
        }


def telemetry_dir(cfg: dict, settings: dict) -> str:
    """Where to look, most specific first.

    1. `payline.reel_stops.telemetry_dir`, for a caller passing settings in directly.
    2. The active game's own `telemetry_dir` in `game_config.json` -- the right home for it,
       since a telemetry folder belongs to a game and not to this stage.
    3. Derived from the active game's process, so a second game needs no edit at all:
       `FortuneOx.exe` becomes `C:\\logs\\Telemetry\\Data\\FortuneOx`.
    """
    configured = (settings.get("reel_stops") or {}).get("telemetry_dir")
    if configured:
        return configured
    game = cfg.get("game") or {}
    if game.get("telemetry_dir"):
        return game["telemetry_dir"]
    process = game.get("process") or ""
    return os.path.join(DEFAULT_DIR_ROOT, os.path.splitext(process)[0] or "")


def _timestamp(text: str) -> dt.datetime | None:
    """The first 19 characters of `2026-08-13T15:58:0905:30`, which is the part that is real.

    The offset is written without its sign, so the tail is dropped rather than guessed at --
    everything this is compared against (a file's mtime) is local time anyway.
    """
    try:
        return dt.datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
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
            stops = [int(n) for n in NUMBER_RE.findall(found.group(1))]
            if not stops:
                continue
            stamp = TIMESTAMP_RE.search(line)
            game = GAME_ID_RE.search(line)
            entries.append(Entry(stops,
                                 _timestamp(stamp.group(1)) if stamp else None,
                                 game.group(1) if game else None,
                                 path, line_no))
    return entries


def newest_file_with_entries(folder: str) -> tuple[str, list[Entry]]:
    """The newest file in `folder` that holds any reel stops, and its entries."""
    if not os.path.isdir(folder):
        raise TelemetryError(
            f"the telemetry folder {folder} does not exist. Set "
            f"payline.reel_stops.telemetry_dir in config.json to the folder the game writes "
            f"its telemetry to, or set payline.reel_stops.enabled to false to audit on the "
            f"pixels alone")

    files = [os.path.join(folder, name) for name in os.listdir(folder)]
    files = [p for p in files if os.path.isfile(p)]
    if not files:
        raise TelemetryError(f"the telemetry folder {folder} is empty")

    # Newest first, and the first one that *has* stops wins -- the client files in this folder
    # have none, and one of them is newer than the server file that does.
    for path in sorted(files, key=os.path.getmtime, reverse=True):
        entries = read_entries(path)
        if entries:
            return path, entries

    raise TelemetryError(
        f"none of the {len(files)} files in {folder} contains a BaseGameReelStops entry. That "
        f"marker is written by the game server's telemetry -- check that the game has played "
        f"at least one spin since it was started")


def pick_entry(entries: list[Entry], frame_time: dt.datetime | None,
               tolerance_s: float = DEFAULT_TOLERANCE_S) -> tuple[Entry, str, bool]:
    """The entry for the frame being audited, how it was chosen, and whether that is *proof*.

    The last entry at or before `frame_time` is this frame's spin (the stops land 3-4 s before
    the frame is shot), and the third element is True only then. With no frame time, or nothing
    inside the tolerance, the last entry in the file is returned with False beside it -- and the
    caller must not decide a COMPARE on it. Run `2026-08-13_114200` is why that flag exists: it
    was captured at 11:42, the telemetry file on disk begins at 12:08, and the nearest thing to
    it is a spin four hours later whose reels have nothing to do with that frame.
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
            f"the last entry in the file -- no stops within {tolerance_s:.0f}s before the "
            f"frame, so this is a different spin"), False
    return entries[-1], "the last entry in the file (the frame has no timestamp to match)", False


def latest_stops(folder: str, frame_path: str | None = None,
                 tolerance_s: float = DEFAULT_TOLERANCE_S) -> dict:
    """The reel stops to audit against, with everything needed to check that choice.

    Returns the entry's own description plus `matched_by`, `entries` (how many were in the
    file) and `frame_time`. Raises `TelemetryError` with the folder named when there is
    nothing to read -- the caller decides whether that is fatal.
    """
    path, entries = newest_file_with_entries(folder)

    frame_time = None
    if frame_path and os.path.isfile(frame_path):
        frame_time = dt.datetime.fromtimestamp(os.path.getmtime(frame_path))

    entry, matched_by, matched = pick_entry(entries, frame_time, tolerance_s)
    record = entry.describe()
    record.update({
        "folder": folder,
        "path": path,
        "entries": len(entries),
        "matched_by": matched_by,
        "matched": matched,
        "frame_time": frame_time.isoformat(sep=" ") if frame_time else None,
    })
    LOG.info("reel stops %s from %s (%s)", entry.stops, os.path.basename(path), matched_by)
    return record
