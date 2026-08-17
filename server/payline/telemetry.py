"""THE CHECKPOINT, part 2 -- where the reel stops come from.

The game logs a spin's landing positions to **its own log** -- the same file `gamelog.py` reads,
named once in `game_config.json` as `games.<exe>.log`:

    08/06/26 00:07:29.861 00 HuffNPuffLink:5760 INF:
        [Slot.HandleSlotReelStoppedMessage] reelsStops[107 93 66 94 95]

one stop per reel, left to right. `reelstrips.py` turns those five numbers into fifteen symbol
names.

**This used to read the platform's telemetry service instead**, out of a folder of its own
(`C:\\logs\\Telemetry\\Data\\<game>`) whose `BaseGameReelStops` carries the same five numbers --
two files, two formats and a `telemetry_dir` setting to find the second one, for a fact the game
log already states. **The swap was measured before it was made**, over every entry both sources
hold on this machine: 595 game-log entries against 593 telemetry entries, paired by timestamp
within 5 s -- **593 agree, 0 disagree**, the game line landing 0.51 s after the telemetry line
(median; -0.03 s to +1.00 s). The two extras are spins the telemetry missed (2026-08-05 11:29 and
2026-08-10 18:03), so the log is a superset rather than a sample. The oracle is unchanged; only
the file it is read from is.

**The marker is anchored on its handler, not on the word.** `reelsStops[` occurs 595 times here
and all 595 are `[Slot.HandleSlotReelStoppedMessage]` lines, so the anchor costs nothing today --
but the same log carries `SlotGameEngine.HandleInternalSlotReelsStoppedMsg`,
`GDK.Common.ServerAPI.LastStopsMsg`, `StopsMsg` and `SyncStopsMsg`, any of which could grow a
similar payload. Narrow matching is `gamelog.EVENTS`' rule for the same reason. It is also
base-game only, which is what the telemetry's `BaseGameReelStops` meant: a feature's free spins
do not add lines here, or the two counts could not have come out at 595 against 593.

**Not every game writes it, and that is reported rather than worked around.** FortuneOx splits
its logging in two and this marker is in `FortuneOx_Server.log`, which `games["FortuneOx.exe"].log`
does not point at -- so for that game the checkpoint stands down with `status: "unavailable"` and
the audit runs on the pixels alone. It did exactly that before this change too, there being no
`C:\\logs\\Telemetry\\Data\\FortuneOx` on this machine either, so no coverage was lost. Pointing
the block at the server log is the fix if it ever matters.

**It rotates**, at ~20 MB into `<stem>-YYYYMMDD-HHMMSS.log` -- `gamelog.py`'s docstring records
the same trap. A frame from before the last rotation has its stops in a *sibling*, so every
sibling is read and the entries merged in time order. Reading only the live log would stand the
checkpoint down on any run older than the last rotation: a silent loss of coverage rather than a
wrong answer, but the rotated file here holds 345 of the 595 entries. Merging costs 0.12 s per
20 MB file.

**Which entry, though, is the question that decides whether the checkpoint is even about the
right spin.** Taking the last one is right when you have just spun -- that is how the mapping in
the request was read off by hand -- and wrong the moment a run folder from earlier in the day is
re-audited, where it would confidently describe a different spin's reels. The timestamps make
that answerable: the stops line lands a few seconds before the `spin_result.png` it belongs to
(1.8 s and 2.5 s on the two run folders on disk; the telemetry it replaced measured 3-4 s across
six consecutive captures, and this marker is logged 0.5 s later than that one). So the default is
**the last entry at or before the frame's own timestamp**, within `DEFAULT_TOLERANCE_S`, and
falling back to the last entry is a *reported* fallback (`matched_by`), never a silent one.
"""

from __future__ import annotations

import datetime as dt
import glob
import logging
import os
import re

from ..settings import resolve

LOG = logging.getLogger("payline")

# How far before the frame an entry may sit and still be taken as that frame's spin. The
# measured gap is a few seconds; 15 minutes is loose on purpose -- it is here to reject *another
# session's* stops, not to police seconds, and a Hold & Spin can put a minute between the
# stop landing and the frame being shot.
DEFAULT_TOLERANCE_S = 900

# Named once, so the error messages below and the docstring cannot drift from the pattern.
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

    One name in one place -- `game_config.json`'s `games.<exe>.log`, the same key
    `capture/gamelog.py` reads. There is deliberately no payline-level override beside it: a
    second setting naming the same file is what `telemetry_dir` was, and a half-done edit that
    left the two pointing at different games read as a working config.

    Ordered by **name**, not mtime. The rotated names carry a sortable timestamp and sort before
    the live log (`-` < `.`), while mtime lies here for the reason `gamelog.py` gives -- the game
    holds the handle open. It is only a stable pre-order anyway; `collect_entries` sorts on each
    line's own clock.
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
    """`08/06/26 00:07:29.861` -> a naive local datetime.

    Month first: `07/29/26` in this log is the 29th of July, which is the only reading 29 can
    take. Everything it is compared against (a frame's mtime) is local time too.
    """
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

    Merged rather than "the newest file that has any", because rotation puts an older run's
    stops in a sibling and `pick_entry` chooses on the frame's own time, not on the file.
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
    # By each line's own clock rather than by file: a rotated sibling can only hold older
    # lines, but the mtime that would say so is unreliable while the game holds the handle
    # open. Undated lines sort first, so the `entries[-1]` fallback lands on a dated one.
    entries.sort(key=lambda e: e.timestamp or dt.datetime.min)
    return entries


def pick_entry(entries: list[Entry], frame_time: dt.datetime | None,
               tolerance_s: float = DEFAULT_TOLERANCE_S) -> tuple[Entry, str, bool]:
    """The entry for the frame being audited, how it was chosen, and whether that is *proof*.

    The last entry at or before `frame_time` is this frame's spin (the stops land a few seconds
    before the frame is shot), and the third element is True only then. With no frame time, or
    nothing inside the tolerance, the last entry is returned with False beside it -- and the
    caller must not decide a COMPARE on it. Run `2026-08-13_114200` is why that flag exists: it
    was captured at 11:42, the telemetry file on disk began at 12:08, and the nearest thing to
    it was a spin four hours later whose reels have nothing to do with that frame.
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
    """The reel stops to audit against, with everything needed to check that choice.

    Returns the entry's own description plus `matched_by`, `entries` (how many were found),
    `logs` (how many files were read) and `frame_time`. Raises `TelemetryError` naming the
    config key when there is nothing to read -- the caller decides whether that is fatal.
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
