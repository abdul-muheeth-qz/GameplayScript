"""The payline rule, checked without images or a cabinet.

    python -m server.payline.test_paylines          (or: python -m pytest server/payline)

Fixtures come from the "FOR LATER ENHANCEMENT" tables on sheet payline1 of Payline.xlsx,
where each cell holds a scalar instead of an embedding. That is the point: `paylines.py`
takes a matcher, so a matcher over numbers proves the five lines are walked correctly
independently of whether the vision layer works.

This and `test_reelstrips.py` are the only parts of this repository that *can* be unit
tested -- capture talks to live Windows APIs and a running game, extract needs Tesseract
and pixels, and validate is exercised by re-running it over the folders already on disk.
So it carries its own runner rather than depending on pytest, which is not installed here,
and prints a pass/fail line each so a failure names itself.
"""

from __future__ import annotations

import sys

from .matcher import Decision
from .paylines import evaluate_line

LINES = [
    {"id": 1, "name": "Middle row", "cells": ["E21", "E22", "E23", "E24", "E25"]},
    {"id": 2, "name": "Top row", "cells": ["E11", "E12", "E13", "E14", "E15"]},
    {"id": 3, "name": "Bottom row", "cells": ["E31", "E32", "E33", "E34", "E35"]},
    {"id": 4, "name": "V shape", "cells": ["E11", "E22", "E33", "E24", "E15"]},
    {"id": 5, "name": "Inverted V", "cells": ["E31", "E22", "E13", "E24", "E35"]},
]


class ScalarMatcher:
    """Stand-in matcher: cells are equal when their scalar values are equal."""

    name = "scalar"

    def __init__(self, values):
        self.values = {k.upper(): v for k, v in values.items()}

    def compare(self, a, b):
        same = self.values[a] == self.values[b]
        return Decision(a, b, 1.0 if same else 0.0, same,
                        f"{self.values[a]} vs {self.values[b]}")

    def labels(self):
        return {k: str(v) for k, v in self.values.items()}


def grid(rows):
    """rows = [[r1..r5], [r1..r5], [r1..r5]] -> {'E11': v, ...}"""
    return {f"E{r}{c}": rows[r - 1][c - 1] for r in range(1, 4) for c in range(1, 6)}


def run(values):
    matcher = ScalarMatcher(values)
    return {line["id"]: evaluate_line(line, matcher).pays for line in LINES}


PAYLINE_TABLE_1 = grid([
    [32.56, 23.53, 54.65, 29.10, 36.53],
    [47.34, 38.67, 83.42, 96.88, 23.26],
    [33.33, 44.44, 55.55, 23.32, 32.56],
])

PAYLINE_TABLE_2 = grid([
    [32.56, 23.53, 32.56, 29.10, 36.53],
    [32.56, 32.56, 83.42, 96.88, 23.26],
    [32.56, 44.44, 55.55, 23.32, 32.56],
])


def test_payline_table_1_nothing_pays():
    """First table: every line breaks on its first pair."""
    assert run(PAYLINE_TABLE_1) == {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}


def test_payline_table_2():
    """Second table: 32.56 repeats at E11, E21, E22, E31, E13, E35.

    Line 1  E21=E22, then E22!=E23            -> pays 2
    Line 2  E11!=E12                          -> pays 0
    Line 3  E31!=E32                          -> pays 0
    Line 4  E11=E22, then E22!=E33            -> pays 2
    Line 5  E31=E22=E13, then E13!=E24        -> pays 3
    """
    assert run(PAYLINE_TABLE_2) == {1: 2, 2: 0, 3: 0, 4: 2, 5: 3}


def test_all_identical_pays_five():
    assert run(grid([[7] * 5] * 3)) == {1: 5, 2: 5, 3: 5, 4: 5, 5: 5}


def test_first_pair_fails_stops_at_zero():
    """'IF NO THEN STOP' -- a later matching run must not rescue the line."""
    values = grid([[1, 2, 9, 9, 9], [0] * 5, [0] * 5])
    assert run(values)[2] == 0


def test_break_is_not_skipped():
    """A mismatch ends the run; matches after it do not count."""
    values = grid([[5, 5, 8, 5, 5], [0] * 5, [0] * 5])
    assert run(values)[2] == 2


def test_four_of_a_kind():
    values = grid([[0] * 5, [3, 3, 3, 3, 4], [0] * 5])
    assert run(values)[1] == 4


def test_step_trace_is_recorded():
    """The audit trail must hold one entry per COMPARE actually performed."""
    matcher = ScalarMatcher(grid([[5, 5, 5, 9, 9], [0] * 5, [0] * 5]))
    result = evaluate_line(LINES[1], matcher)
    assert result.pays == 3
    assert len(result.steps) == 3
    assert [s.match for s in result.steps] == [True, True, False]
    assert result.message == "LINE 2 PAYS 3"
    assert result.winning_cells == ["E11", "E12", "E13"]


def test_v_and_inverted_v_paths():
    """Lines 4 and 5 must read the diagonal cells, not the straight rows."""
    values = grid([[1, 0, 1, 0, 1], [0, 1, 0, 1, 0], [1, 0, 1, 0, 1]])
    out = run(values)
    assert out[4] == 5 and out[5] == 5
    assert out[1] == 0 and out[2] == 0 and out[3] == 0


def test_geometry_paylines_are_the_five_lines():
    """The shipped geometry must define the same five lines these fixtures assume."""
    from .geometry import DEFAULT_PAYLINES

    assert [line["cells"] for line in DEFAULT_PAYLINES] == [line["cells"] for line in LINES]


def test_geometry_rejects_an_overlapping_reel():
    """Two cells may never share pixels -- pixel_box clamps, so this has to raise here."""
    from .geometry import Geometry, PaylineError, configured_games

    block = dict(configured_games()["FortuneOx.exe"])
    block["reel_bounds"] = [[0.0, 0.5], [0.4, 1.0]]
    try:
        Geometry("Test.exe", block)
    except PaylineError as exc:
        assert "overlap" in str(exc)
    else:
        raise AssertionError("an overlapping reel_bounds was accepted")


def test_geometry_names_a_missing_key():
    """The blocks are hand-edited JSON now, so an incomplete one must say which key."""
    from .geometry import Geometry, PaylineError, configured_games

    block = {k: v for k, v in configured_games()["FortuneOx.exe"].items()
             if k != "row_bounds"}
    try:
        Geometry("Test.exe", block)
    except PaylineError as exc:
        assert "row_bounds" in str(exc) and "game_config.json" in str(exc)
    else:
        raise AssertionError("a block with no row_bounds was accepted")


def test_shipped_geometry_validates():
    """Every payline_geometry block in game_config.json, whatever `active` says."""
    from .geometry import Geometry, configured_games

    blocks = configured_games()
    assert blocks, "game_config.json has no payline_geometry block at all"
    for process, block in blocks.items():
        geometry = Geometry(process, block)
        assert geometry.rows >= 1 and geometry.reels >= 2
        assert len(geometry.cells) == geometry.rows * geometry.reels


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
