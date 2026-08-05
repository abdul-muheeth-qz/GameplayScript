#!/usr/bin/env python
"""The platform's telemetry files: what a spin actually paid.

`C:\\logs\\Telemetry\\Data\\` holds two streams the game log cannot supply:

**The per-spin ledger**, `PS\\Game_Play-*.txt` -- one JSON object per spin, written when the spin
ends, carrying the numbers: `Amount_Won`, `Total_Bet`, `Denom`, `Initial_Credit`, `Ending_Credit`,
`Handpay_Win`, `Jackpot_Handpay_Win`, and a `Game_ID` that ties it to everything else. This is the
authoritative answer to "did that spin win, and how much" -- the game's own log never states an
amount.

**The gamble and GDK stream**, `HuffNPuffLink\\HuffNPuffLink_TELEMETRY-*.log` -- the detail of a
gamble round (`InitialGambleIn`, `PlayerWonCash`, `CurrentWin`, `NumGamblesPlayed`) and named
events including `Wager Saver Offered` / `Accepted` / `Rejected`.

Three things about these files that are not obvious:

1. **The active file has a timestamp in its name** (`-20260804-223618`), unlike the game log, so
   the newest match has to be found rather than configured. A rotation starts a *new name*, which
   is why `Feed` re-checks for a newer file instead of holding one path forever.
2. **`Game_Play` timestamps are well formed** (`2026-08-05T15:59:34+0530`) but the game telemetry's
   are **not**: `2026-08-05T15:59:1805:30` is missing the `+` before the offset, so
   `datetime.fromisoformat` rejects it.
3. **Their modification times lie**, the same trap as the game log: `Game_Play` reported a
   timestamp 16 hours older than the entry at the end of it, because the writer holds the handle.
"""

from __future__ import annotations

import glob
import json
import os
import re
from datetime import datetime

import logtail

DEFAULT_ROOT = r"C:\logs\Telemetry\Data"
LEDGER_GLOB = os.path.join("PS", "Game_Play-*.txt")
GAME_GLOB = os.path.join("{theme}", "{theme}_TELEMETRY-*.log")

# "2026-08-05T15:59:18" then either "+0530" or, in the game telemetry, a bare "05:30".
_TS_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)")


class TelemetryError(RuntimeError):
    pass


def _parse_time(raw: str) -> datetime | None:
    match = _TS_RE.match(str(raw))
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _money(raw) -> float | None:
    """"$1,234.50" -> 1234.5. The ledger writes every amount as a formatted string."""
    if raw is None:
        return None
    try:
        return float(str(raw).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def newest(pattern: str) -> str | None:
    """The most recently modified file matching the glob, or None."""
    matches = glob.glob(pattern)
    # mtime is unreliable for *reading* these files but fine for picking the newest name, since
    # a rotation creates a new file and the old one stops being touched.
    return max(matches, key=os.path.getmtime) if matches else None


class Feed:
    """New JSON lines from whichever file currently matches the glob."""

    def __init__(self, pattern: str):
        self.pattern = pattern
        self.path = newest(pattern)
        self._tail = logtail.LogTail(self.path) if self.path else None

    def _reopen_if_rotated(self) -> None:
        latest = newest(self.pattern)
        if latest and latest != self.path:
            # A new file, so start at its beginning: everything in it is new to us.
            self.path, self._tail = latest, logtail.LogTail(latest)

    def mark(self) -> None:
        if self._tail:
            self._tail.mark()

    def poll(self) -> list[dict]:
        self._reopen_if_rotated()
        if not self._tail:
            return []
        return _decode(self._tail.read_new())

    def history(self, limit: int = 2 * 1024 * 1024) -> list[dict]:
        return _decode(self._tail.tail(limit)) if self._tail else []


def _decode(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            # The game telemetry writes bare True/False/None in places; not worth a tolerant
            # parser when the ledger -- the file that carries the numbers -- is strict JSON.
            continue
    return out


class Ledger:
    """The per-spin outcome records, newest last."""

    def __init__(self, root: str = DEFAULT_ROOT):
        pattern = os.path.join(root, LEDGER_GLOB)
        self.feed = Feed(pattern)
        if not self.feed.path:
            raise TelemetryError(
                f"no spin ledger found matching {pattern}. Set \"telemetry.root\" in "
                "config.json, or run with --no-telemetry.")
        self.path = self.feed.path

    @staticmethod
    def summarise(record: dict) -> dict:
        """The fields worth keeping, as numbers."""
        won = _money(record.get("Amount_Won"))
        return {
            "game_id": record.get("Game_ID"),
            "at": (_parse_time(record.get("Timestamp_ISO8601")) or "").__str__() or None,
            "total_bet": _money(record.get("Total_Bet")),
            "denom": _money(record.get("Denom")),
            "amount_won": won,
            "won": bool(won),
            "credit_before": _money(record.get("Initial_Credit")),
            "credit_after": _money(record.get("Ending_Credit")),
            "handpay": bool(record.get("Handpay_Win")),
            "jackpot_handpay": bool(record.get("Jackpot_Handpay_Win")),
            "autoplay": bool(record.get("Autoplay")),
            "spin_button": bool(record.get("Spin_Button_Pressed")),
            "paytable": record.get("Paytable_Name"),
        }

    def mark(self) -> None:
        self.feed.mark()

    def poll(self) -> list[dict]:
        """Summaries of spins that have ended since the last mark/poll."""
        return [self.summarise(r) for r in self.feed.poll()]

    def latest(self) -> dict | None:
        records = self.feed.history()
        return self.summarise(records[-1]) if records else None


class GameFeed:
    """Gamble rounds and named GDK events from the game's own telemetry."""

    def __init__(self, root: str = DEFAULT_ROOT, theme: str = "HuffNPuffLink"):
        self.feed = Feed(os.path.join(root, GAME_GLOB.format(theme=theme)))
        self.path = self.feed.path

    def mark(self) -> None:
        self.feed.mark()

    def poll(self) -> list[dict]:
        """`{"kind": "gamble"|"gdk_event", ...}` for each interesting record."""
        out = []
        for record in self.feed.poll():
            event = record.get("Event") or {}
            at = _parse_time(record.get("Timestamp_ISO8601"))
            common = {"game_id": record.get("Game_Id"), "at": str(at) if at else None}
            if "Gamble" in event:
                out.append({"kind": "gamble", **common, **(event["Gamble"] or {})})
            elif "GDKEvent" in event:
                out.append({"kind": "gdk_event", **common, "event": event["GDKEvent"]})
        return out


# -- CLI -------------------------------------------------------------------


def main(argv=None) -> int:
    import argparse
    import sys
    import time

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--theme", default="HuffNPuffLink")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--last", type=int, metavar="N", help="the last N spins from the ledger")
    mode.add_argument("--watch", action="store_true", help="print spins and gambles as they land")
    args = parser.parse_args(argv)

    try:
        ledger = Ledger(args.root)
    except TelemetryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    games = GameFeed(args.root, args.theme)

    if args.last:
        print(f"ledger: {ledger.path}\n")
        print(f"{'time':<20} {'bet':>8} {'denom':>6} {'won':>9} {'credit after':>13}  flags")
        for record in [ledger.summarise(r) for r in ledger.feed.history()][-args.last:]:
            flags = " ".join(f for f, on in (("HANDPAY", record["handpay"]),
                                             ("JACKPOT", record["jackpot_handpay"]),
                                             ("autoplay", record["autoplay"]),
                                             ("spin-button", record["spin_button"])) if on)
            print(f"{str(record['at']):<20} {record['total_bet'] or 0:>8.2f} "
                  f"{record['denom'] or 0:>6.2f} {record['amount_won'] or 0:>9.2f} "
                  f"{record['credit_after'] or 0:>13.2f}  {flags}")
        return 0

    print(f"ledger: {ledger.path}\ngame:   {games.path}\nCtrl+C to stop", flush=True)
    try:
        while True:
            for record in ledger.poll():
                won = record["amount_won"] or 0
                print(f"spin  bet {record['total_bet']:.2f}  "
                      + (f"WON {won:.2f}" if won else "no win")
                      + f"  credit {record['credit_after']:.2f}", flush=True)
            for record in games.poll():
                print(f"{record['kind']}  "
                      + ", ".join(f"{k}={v}" for k, v in record.items()
                                  if k not in ("kind", "game_id", "at")), flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
