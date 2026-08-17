"""The reel-stop checkpoint, checked without a cabinet, a model or a running game.

    python -m server.payline.test_reelstrips      (or: python -m pytest server/payline)

Same shape and the same reason as `test_paylines.py`: the mapping from a spin's reel stops to
symbol names is pure data, so it is testable, and it is the part of the checkpoint that decides
verdicts. The fixtures are the two spins the rule was measured on --

  * the mapping supplied with the request, stops `[24, 79, 153, 25, 0]`, all fifteen names;
  * run `2026-08-13_153618`, stops `[86, 121, 127, 138, 86]`, which was checked 15/15 against
    its own contact sheet and is the spin whose inverted V the checkpoint has to fix;

-- plus the band, the abstentions, and the game-log reader: that it takes the marker only from
its own handler and not from the other stops-flavoured messages beside it, that it merges the
rotated siblings, and that it names `games.<exe>.log` rather than guessing when there is
nothing to read. It reads `server/assets/payline_excel.xlsx`; that file is the fixture.
"""

from __future__ import annotations

import os
import sys
import tempfile

from . import reelstrips, telemetry
from .geometry import PaylineError, geometry_for_game
from .matcher import Decision, ReelStopMatcher

# The mapping supplied with the request, read off the game's own screen.
SUPPLIED_STOPS = [24, 79, 153, 25, 0]
SUPPLIED_GRID = {
    "E11": "Pisces", "E12": "King", "E13": "Mystery1", "E14": "Ox", "E15": "Arm Band",
    "E21": "Pisces", "E22": "Arm Band", "E23": "Queen", "E24": "Ten", "E25": "Jack",
    "E31": "Arm Band", "E32": "Arm Band", "E33": "Pisces", "E34": "Wealth Pot",
    "E35": "Orb (Splittable)",
}

# Run 2026-08-13_153618, verified cell by cell against payline/tiles/contact_sheet.png.
RUN_153618_STOPS = [86, 121, 127, 138, 86]
RUN_153618_GRID = {
    "E11": "Jack", "E12": "Jack", "E13": "Arm Band", "E14": "Arm Band", "E15": "Jack",
    "E21": "Arm Band", "E22": "Arm Band", "E23": "Arm Band", "E24": "Arm Band",
    "E25": "Arm Band",
    "E31": "Arm Band", "E32": "Free Games", "E33": "Queen", "E34": "Ace", "E35": "Arm Band",
}


def strips():
    return reelstrips.load_strips()


def geometry():
    """FortuneOx's shipped block, by name -- `active` may be pointing at another game."""
    return geometry_for_game("FortuneOx.exe")


class FixedMatcher:
    """An inner matcher whose cosine for every pair is whatever the test says it is."""

    name = "fixed"

    def __init__(self, similarity, threshold=0.90):
        self.embeddings = {}
        self.sim = similarity
        self.threshold = threshold

    def describe(self):
        return f"fixed ({self.sim})"

    def labels(self):
        return {}

    def compare(self, a, b):
        ok = self.sim >= self.threshold
        return Decision(a, b, self.sim, ok, f"cos {self.sim:.4f}")


# -- the spreadsheet -------------------------------------------------------

def test_strips_are_five_reels_of_two_hundred():
    """200 positions, 0..199. The sheet's row 203 holds an 'X' terminator, not a symbol."""
    lengths = strips().lengths()
    assert sorted(lengths) == [1, 2, 3, 4, 5], lengths
    assert set(lengths.values()) == {200}, lengths


def test_terminator_is_never_a_symbol():
    for reel, strip in strips().strips.items():
        assert reelstrips.TERMINATOR not in strip, f"reel {reel} kept the terminator"


def test_supplied_mapping():
    """The mapping given with the request, reproduced from the sheet alone."""
    got = strips().grid(SUPPLIED_STOPS, 3, 5)
    assert got == SUPPLIED_GRID, {k: (v, SUPPLIED_GRID[k])
                                  for k, v in got.items() if v != SUPPLIED_GRID[k]}


def test_run_153618_mapping():
    """The captured spin the checkpoint was built against: five Arm Bands across the middle."""
    got = strips().grid(RUN_153618_STOPS, 3, 5)
    assert got == RUN_153618_GRID, {k: (v, RUN_153618_GRID[k])
                                    for k, v in got.items() if v != RUN_153618_GRID[k]}


def test_rows_are_stop_then_next_two():
    """Row 1 is the stop itself; rows 2 and 3 are the next positions down the strip."""
    s = strips()
    for reel in range(1, 6):
        strip = s.strips[reel]
        for row in range(1, 4):
            assert s.symbol(reel, 7, row) == strip[7 + row - 1]


def test_strip_wraps_at_the_end():
    """A stop of 199 shows 199, 0, 1 -- getting this wrong is invisible on ordinary stops."""
    s = strips()
    strip = s.strips[1]
    assert s.symbol(1, 199, 1) == strip[199]
    assert s.symbol(1, 199, 2) == strip[0]
    assert s.symbol(1, 199, 3) == strip[1]


def test_too_few_stops_is_named():
    try:
        strips().grid([1, 2, 3], 3, 5)
    except PaylineError as exc:
        assert "reel stops" in str(exc) and "5 reels" in str(exc)
    else:
        raise AssertionError("four missing reels were accepted")


def test_missing_spreadsheet_names_the_setting():
    try:
        reelstrips.load_strips(os.path.join(tempfile.gettempdir(), "no_such_sheet.xlsx"))
    except PaylineError as exc:
        assert "payline.reel_stops.strips" in str(exc)
    else:
        raise AssertionError("a missing spreadsheet was accepted")


def test_placeholders_are_the_mystery_symbols():
    """Mystery symbols reveal as other art; WILD is drawn as itself and must stay comparable."""
    assert reelstrips.is_placeholder("Mystery1")
    assert reelstrips.is_placeholder("mystery (orb)")
    assert not reelstrips.is_placeholder("WILD")
    assert not reelstrips.is_placeholder("Arm Band")
    assert not reelstrips.is_placeholder(None)


# -- the checkpoint --------------------------------------------------------

def test_checkpoint_overturns_the_inverted_v():
    """The bug this exists for: E31 vs E22, both Arm Band, cos 0.7622, threshold 0.90."""
    grid = strips().grid(RUN_153618_STOPS, 3, 5)
    matcher = ReelStopMatcher(FixedMatcher(0.7622), grid, (0.70, 0.90))
    decision = matcher.compare("E31", "E22")
    assert decision.match is True
    assert matcher.overrides() == 1
    assert "Arm Band" in decision.checkpoint


def test_checkpoint_confirms_a_no():
    """It is not a rubber stamp: different names in the band stay NO."""
    grid = strips().grid(RUN_153618_STOPS, 3, 5)
    matcher = ReelStopMatcher(FixedMatcher(0.85), grid, (0.70, 0.90))
    assert matcher.compare("E31", "E32").match is False       # Arm Band vs Free Games
    assert matcher.overrides() == 0


def test_confident_readings_are_never_touched():
    """Outside the band the pixels stand, whatever the telemetry says."""
    grid = strips().grid(RUN_153618_STOPS, 3, 5)
    for sim in (0.15, 0.69, 0.9999):
        matcher = ReelStopMatcher(FixedMatcher(sim), grid, (0.70, 0.90))
        decision = matcher.compare("E31", "E32")             # different symbols
        assert decision.match == (sim >= 0.90)
        assert decision.checkpoint == ""
        assert matcher.adjudications == []


def test_mystery_abstains_and_says_so():
    """A mystery name cannot decide a pair -- the frame may be showing anything."""
    grid = strips().grid(SUPPLIED_STOPS, 3, 5)                 # E13 is Mystery1
    matcher = ReelStopMatcher(FixedMatcher(0.80), grid, (0.70, 0.90))
    decision = matcher.compare("E13", "E23")
    assert decision.match is False                             # the inner verdict, unchanged
    assert matcher.overrides() == 0
    assert matcher.adjudications[0]["decided"] is False
    assert "mystery" in decision.checkpoint.lower()


def test_no_grid_abstains():
    """No telemetry means the audit still runs, on the pixels alone."""
    matcher = ReelStopMatcher(FixedMatcher(0.80), {}, (0.70, 0.90))
    decision = matcher.compare("E31", "E22")
    assert decision.match is False
    assert matcher.adjudications[0]["decided"] is False


def test_labels_come_from_the_stops():
    """The grid is what names the symbols in the record and on the annotated images."""
    grid = strips().grid(RUN_153618_STOPS, 3, 5)
    matcher = ReelStopMatcher(FixedMatcher(0.99), grid, (0.70, 0.90))
    assert matcher.labels()["E22"] == "Arm Band"


def test_paylines_pay_what_the_stops_say():
    """End to end over the rule itself: run 153618's five lines, from the stops alone."""
    from .paylines import evaluate_all

    grid = strips().grid(RUN_153618_STOPS, 3, 5)

    class NameMatcher:
        """Every pair lands in the band, so every pair is the checkpoint's to decide."""
        name = "names"
        embeddings = {}
        threshold = 0.90

        def describe(self):
            return "names"

        def labels(self):
            return {}

        def compare(self, a, b):
            return Decision(a, b, 0.80, False, "in the band")

    matcher = ReelStopMatcher(NameMatcher(), grid, (0.70, 0.90))
    pays = [r.pays for r in evaluate_all(geometry(), matcher)]
    # Middle row is five Arm Bands; the inverted V (E31 E22 E13 E24 E35) is five Arm Bands too;
    # the top row breaks at E13, the bottom at E32, and the V at E11 vs E22 (Jack vs Arm Band).
    assert pays == [5, 2, 0, 0, 5], pays


# -- the game log ----------------------------------------------------------

# Real line shapes, including three decoys the narrow marker has to stay off: two other
# stops-flavoured messages the same log carries, and a bare `reelsStops[` from a handler that
# is not the one this reads.
SAMPLE_LOG = (
    "08/13/26 15:54:33.120 00 FortuneOx:4210 INF: [GameStateMachine.PayWin] payWin[0]\n"
    "08/13/26 15:54:37.884 00 FortuneOx:4210 INF: "
    "[GDK.Common.ServerAPI.LastStopsMsg] reelsStops[1 2 3 4 5]\n"
    "08/13/26 15:58:06.001 00 FortuneOx:4210 INF: "
    "[SlotGameEngine.HandleInternalSlotReelsStoppedMsg] reelsStops[9 9 9 9 9]\n"
    "08/13/26 15:51:03.417 00 FortuneOx:4210 INF: "
    "[Slot.HandleSlotReelStoppedMessage] reelsStops[135 89 15 144 75]\n"
    "08/13/26 15:58:09.902 00 FortuneOx:4210 INF: "
    "[Slot.HandleSlotReelStoppedMessage] reelsStops[24 79 153 25 0]\n"
)

LOG_NAME = "FortuneOx_Client.log"


def _sample_file():
    path = os.path.join(tempfile.mkdtemp(), LOG_NAME)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(SAMPLE_LOG)
    return path


def test_entries_are_read_from_the_game_log():
    """The five numbers, the line's own clock, and nothing from the decoy handlers.

    Anchoring on `Slot.HandleSlotReelStoppedMessage` rather than on `reelsStops[` is what
    keeps `LastStopsMsg` and `HandleInternalSlotReelsStoppedMsg` out -- both are real markers
    in this log, and both would otherwise be read as this spin's stops.
    """
    entries = telemetry.read_entries(_sample_file())
    assert [e.stops for e in entries] == [[135, 89, 15, 144, 75], SUPPLIED_STOPS]
    assert entries[-1].timestamp.isoformat() == "2026-08-13T15:58:09.902000"


def test_the_frames_own_spin_is_picked_not_the_last():
    """Re-auditing an older frame must not be judged against the newest spin's reels."""
    import datetime as dt

    entries = telemetry.read_entries(_sample_file())
    frame = dt.datetime(2026, 8, 13, 15, 51, 7)               # 4s after the earlier entry
    entry, why, matched = telemetry.pick_entry(entries, frame)
    assert entry.stops == [135, 89, 15, 144, 75]
    assert "frame" in why and matched is True


def test_falling_back_to_the_last_entry_is_reported():
    import datetime as dt

    entries = telemetry.read_entries(_sample_file())
    frame = dt.datetime(2026, 8, 12, 10, 0, 0)                # the day before: nothing before it
    entry, why, matched = telemetry.pick_entry(entries, frame)
    assert entry.stops == SUPPLIED_STOPS
    assert "a different spin" in why
    # False is what makes the checkpoint stand down rather than judge the wrong spin.
    assert matched is False


def test_no_frame_time_takes_the_last_entry():
    entries = telemetry.read_entries(_sample_file())
    entry, why, matched = telemetry.pick_entry(entries, None)
    assert entry.stops == SUPPLIED_STOPS and "last entry" in why and matched is False


def test_rotated_siblings_are_merged_in_time_order():
    """The log rotates at ~20 MB, so an older frame's stops are in a sibling, not the live file.

    Reading only the configured path would stand the checkpoint down on any run from before the
    last rotation -- a silent loss of coverage. The merge is ordered by each line's own clock
    rather than by mtime, which lies while the game holds the handle open.
    """
    folder = tempfile.mkdtemp()
    live = os.path.join(folder, LOG_NAME)
    with open(live, "w", encoding="utf-8") as fh:
        fh.write(SAMPLE_LOG)
    rotated = os.path.join(folder, "FortuneOx_Client-20260729-164741.log")
    with open(rotated, "w", encoding="utf-8") as fh:
        fh.write("07/29/26 16:59:27.795 00 FortuneOx:9 INF: "
                 "[Slot.HandleSlotReelStoppedMessage] reelsStops[70 91 110 114 86]\n")
    # A neighbour that is not a rotation of this log must not be swept in.
    with open(os.path.join(folder, "SomethingElse.log"), "w", encoding="utf-8") as fh:
        fh.write("07/29/26 17:00:00.000 00 X:9 INF: "
                 "[Slot.HandleSlotReelStoppedMessage] reelsStops[1 1 1 1 1]\n")

    paths = telemetry.game_logs({"game": {"log": live}})
    assert sorted(os.path.basename(p) for p in paths) == [
        "FortuneOx_Client-20260729-164741.log", LOG_NAME]

    entries = telemetry.collect_entries(paths)
    assert [e.stops for e in entries] == [
        [70, 91, 110, 114, 86], [135, 89, 15, 144, 75], SUPPLIED_STOPS]


def test_checkpoint_stands_down_for_another_spin():
    """A frame with no stops of its own must be audited on the pixels, not on someone else's.

    This is run `2026-08-13_114200` in miniature: a frame older than every entry in the file.
    The stops that *were* found are still reported, so the record can say which spin it
    declined to use, but the matcher handed back must be the unwrapped one.
    """
    import datetime as dt

    from .matcher import build_checkpoint

    folder = tempfile.mkdtemp()
    log = os.path.join(folder, LOG_NAME)
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(SAMPLE_LOG)
    frame = os.path.join(folder, "spin_result.png")
    with open(frame, "wb") as fh:
        fh.write(b"")
    old = dt.datetime(2026, 8, 12, 10, 0, 0).timestamp()
    os.utime(frame, (old, old))

    inner = FixedMatcher(0.80)
    cfg = {"game": {"log": log}}
    settings = {"reel_stops": {}, "thresholds": {"pixel": 0.90}}
    matcher, record = build_checkpoint(inner, geometry(), cfg, settings, "pixel", frame)
    assert matcher is inner, "the checkpoint judged a frame it could not identify"
    assert record["status"] == "unavailable"
    assert record["stops"] == SUPPLIED_STOPS          # reported, but not used
    assert "allow_latest_fallback" in record["detail"]

    settings["reel_stops"]["allow_latest_fallback"] = True
    matcher, record = build_checkpoint(inner, geometry(), cfg, settings, "pixel", frame)
    assert matcher is not inner and record["status"] == "on"
    assert record["matched"] is False                 # opted into, and still said out loud


def test_checkpoint_runs_for_a_frame_it_can_identify():
    """The other half of the guard: a frame shot just after a spin gets the checkpoint."""
    import datetime as dt

    from .matcher import build_checkpoint

    folder = tempfile.mkdtemp()
    log = os.path.join(folder, LOG_NAME)
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(SAMPLE_LOG)
    frame = os.path.join(folder, "spin_result.png")
    with open(frame, "wb") as fh:
        fh.write(b"")
    shot = dt.datetime(2026, 8, 13, 15, 51, 7).timestamp()      # 4s after the 15:51:03 entry
    os.utime(frame, (shot, shot))

    settings = {"reel_stops": {}, "thresholds": {"pixel": 0.90}}
    matcher, record = build_checkpoint(FixedMatcher(0.80), geometry(),
                                       {"game": {"log": log}}, settings, "pixel", frame)
    assert record["status"] == "on" and record["matched"] is True
    assert record["stops"] == [135, 89, 15, 144, 75]
    assert record["band"] == [0.70, 0.90]                        # high tracks the threshold
    assert matcher.labels()["E11"]                               # names come from the stops


def test_a_missing_log_names_the_setting():
    missing = os.path.join(tempfile.gettempdir(), "no_such_game_log_here", "Game.log")
    try:
        telemetry.game_logs({"game": {"log": missing}})
    except telemetry.TelemetryError as exc:
        assert "games.<exe>.log" in str(exc) and missing in str(exc)
    else:
        raise AssertionError("a missing game log was accepted")


def test_a_game_with_no_log_is_refused_rather_than_guessed_at():
    """There is no second place to look any more, and that is the point.

    The stops used to come from a telemetry folder found by `payline.reel_stops.telemetry_dir`,
    falling back to a path derived from the process name. Two settings for one file is what
    `settings.py` calls a half-done edit waiting to happen, so the game log -- which the capture
    stage already reads -- is now the only source, and a game block without one says so.
    """
    for cfg in ({}, {"game": {}}, {"game": {"process": "FortuneOx.exe", "log": None}}):
        try:
            telemetry.game_logs(cfg)
        except telemetry.TelemetryError as exc:
            assert "games.<exe>.log" in str(exc)
        else:
            raise AssertionError(f"a game with no log was accepted: {cfg}")


def test_a_log_with_no_stops_names_the_split_log_trap():
    """FortuneOx writes this marker to its *server* log, which `log` does not point at."""
    folder = tempfile.mkdtemp()
    log = os.path.join(folder, LOG_NAME)
    with open(log, "w", encoding="utf-8") as fh:
        fh.write("08/13/26 15:54:33.120 00 FortuneOx:4210 INF: [GameOverMsg] done\n")
    try:
        telemetry.collect_entries(telemetry.game_logs({"game": {"log": log}}))
    except telemetry.TelemetryError as exc:
        assert "server" in str(exc) and telemetry.MARKER in str(exc)
    else:
        raise AssertionError("a log with no reel stops was accepted")


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__}  {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
