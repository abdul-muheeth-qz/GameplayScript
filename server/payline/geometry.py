"""Where the reels are, and what a payline is -- the rule, over numbers a person edits.

The numbers are `games.<exe>.payline_geometry` in game_config.json, beside that game's `meter_roi`
and `targets`, so one `active` line decides all of them. This module keeps the rule: what a valid
block is, what a payline is, and the refusal to guess.

Every number is a fraction, never a pixel, at three nested levels -- `reels_roi` of the FRAME,
`reel_bounds`/`row_bounds` of that ROI, `inner_margin_frac` of each CELL. That is what survives a
bigger screen: the same frame from 0.6x to 3.0x reads the identical grid, where a flat 8 px margin
would be a fifth of a cell at one end and a fortieth at the other.

**Keyed by the active game, and a game with no block raises.** Fractions survive a change of scale,
not of aspect ratio or of game art -- FortuneOx's reel window on HuffNPuffLink's portrait frame
lands on unrelated pixels and reports a confident grid. Never fall back to another game's numbers.

Adding a game is `--profile` plus a block, and no code edit. `--profile` reports rather than
detects: two auto-detection approaches were measured and failed, and `tiles.profile`'s docstring
records how so neither is retried.
"""

from __future__ import annotations

import json

from ..settings import DEFAULT_GAME_CONFIG


class PaylineError(Exception):
    """Something went wrong in a way the user can act on."""


# The Payline.xlsx rule set: five lines over a 3x5 grid, cells E{row}{reel} with row 1 at the top
# and reel 1 at the left. A game whose paytable differs sets its own `paylines`, and **the shipped
# block now does state these explicitly** -- they are the spec's lines and not FortuneOx's real
# paytable, so a config that leans on a code literal for them reads as though the paytable had been
# checked. This stays as the default for a block that omits the key, because it is the rule set
# `paylines.py` and `test_paylines.py` were written and verified against.



# The key a game's block carries its reel geometry under, in game_config.json.
GEOMETRY_KEY = "payline_geometry"

# The three with no safe default. `label` and `inner_margin_frac` have one.
REQUIRED_KEYS = ("reels_roi", "reel_bounds", "row_bounds")


class Geometry:
    """One game's reel geometry, validated. Read-only after construction."""

    def __init__(self, process: str, block: dict):
        self.process = process
        self.label = block.get("label", "unnamed")
        self.measured_on = block.get("measured_on")

        # Hand-edited JSON, so a missing key or a quoted number arrives at runtime rather than being
        # a typo caught while writing a literal. Named against the key to fix, or the alternative is
        # a KeyError on the browser's screen.
        where = f"payline geometry for {process} ({self.label})"
        missing = [key for key in REQUIRED_KEYS if key not in block]
        if missing:
            raise PaylineError(
                f"{where}: {GEOMETRY_KEY} in game_config.json has no "
                f"{', '.join(repr(k) for k in missing)}. A block needs "
                f"{', '.join(REQUIRED_KEYS)}; measure them with "
                f"`python -m server.payline.cli --profile <a spin_result.png> X0 Y0 X1 Y1`")
        try:
            self.reels_roi = [float(v) for v in block["reels_roi"]]
            self.reel_bounds = [[float(v) for v in pair] for pair in block["reel_bounds"]]
            self.row_bounds = [[float(v) for v in pair] for pair in block["row_bounds"]]
            self.inner_margin_frac = float(block.get("inner_margin_frac", 0.0))
        except (TypeError, ValueError):
            raise PaylineError(
                f"{where}: every number in {GEOMETRY_KEY} must be a plain JSON number, and "
                f"reel_bounds/row_bounds a list of [start, end] pairs. Got reels_roi="
                f"{block.get('reels_roi')!r}, reel_bounds={block.get('reel_bounds')!r}, "
                f"row_bounds={block.get('row_bounds')!r}, inner_margin_frac="
                f"{block.get('inner_margin_frac')!r}") from None

        self.paylines = block.get("paylines") 
        self._validate()

    @property
    def reels(self) -> int:
        return len(self.reel_bounds)

    @property
    def rows(self) -> int:
        return len(self.row_bounds)

    @property
    def grid_label(self) -> str:
        return f"{self.rows}x{self.reels}"

    @property
    def cells(self) -> tuple[str, ...]:
        """Every cell name, reading across each row in turn."""
        return tuple(cell_name(r, c)
                     for r in range(1, self.rows + 1)
                     for c in range(1, self.reels + 1))

    def _validate(self):
        """Refuse numbers that would otherwise crop silently wrong.

        `geometry.pixel_box` clamps to the image, so a fraction above 1.0 or an inverted pair does
        not raise on its own -- it returns a crop of the frame's edge with nothing to say why.
        """
        where = f"payline geometry for {self.process} ({self.label})"

        if len(self.reels_roi) != 4:
            raise PaylineError(f"{where}: reels_roi needs four numbers "
                               f"[x0, y0, x1, y1], got {self.reels_roi!r}")
        x0, y0, x1, y1 = self.reels_roi
        if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
            raise PaylineError(
                f"{where}: reels_roi {self.reels_roi!r} is not a box inside the frame. "
                f"These are fractions of the frame's width and height, so each must be "
                f"0.0-1.0 with x0 < x1 and y0 < y1")

        for axis, bounds in (("reel_bounds", self.reel_bounds),
                             ("row_bounds", self.row_bounds)):
            if len(bounds) < 2:
                raise PaylineError(f"{where}: {axis} needs at least two entries, got "
                                   f"{len(bounds)}")
            for i, pair in enumerate(bounds):
                if len(pair) != 2:
                    raise PaylineError(f"{where}: {axis}[{i}] is {pair!r}, not a "
                                       f"[start, end] pair")
                a, b = pair
                if not (0.0 <= a < b <= 1.0):
                    raise PaylineError(
                        f"{where}: {axis}[{i}] is {pair!r}, which is not inside the ROI. "
                        f"These are fractions OF THE ROI (0.0 is its left/top edge, 1.0 "
                        f"its right/bottom), not of the whole frame")
            for i in range(1, len(bounds)):
                if bounds[i][0] < bounds[i - 1][1]:
                    raise PaylineError(
                        f"{where}: {axis}[{i}] starts at {bounds[i][0]} but "
                        f"{axis}[{i - 1}] runs to {bounds[i - 1][1]} -- they overlap, so "
                        f"two cells would share pixels. Order them left to right (rows: "
                        f"top to bottom) and leave the gutters as the gaps between them")

        if not 0.0 <= self.inner_margin_frac < 0.5:
            raise PaylineError(
                f"{where}: inner_margin_frac is {self.inner_margin_frac!r}; it is the "
                f"fraction of each CELL trimmed off every side, so it must be 0.0-0.5 "
                f"(0.5 would trim the cell away entirely)")

        known = set(self.cells)
        if not self.paylines:
            raise PaylineError(f"{where}: no paylines defined")
        seen_ids = set()
        for line in self.paylines:
            for key in ("id", "cells"):
                if key not in line:
                    raise PaylineError(f"{where}: a payline has no {key!r}")
            if line["id"] in seen_ids:
                raise PaylineError(f"{where}: two paylines share id {line['id']}")
            seen_ids.add(line["id"])
            if len(line["cells"]) < 2:
                raise PaylineError(f"{where}: payline {line['id']} needs at least two "
                                   f"cells -- there is nothing to COMPARE otherwise")
            for cell in line["cells"]:
                if cell.upper() not in known:
                    raise PaylineError(
                        f"{where}: payline {line['id']} names cell {cell!r}, which does "
                        f"not exist on a {self.rows}x{self.reels} grid. Cells are "
                        f"E<row><reel>, row 1 at the top and reel 1 at the left")

    def describe(self) -> dict:
        """What the record should say about the geometry a reading came from."""
        return {"process": self.process, "label": self.label,
                "measured_on": self.measured_on,
                "grid": self.grid_label,
                "reels_roi": self.reels_roi,
                "inner_margin_frac": self.inner_margin_frac}


def cell_name(row: int, reel: int) -> str:
    """(2, 3) -> 'E23'. Row 1 is the top, reel 1 is the leftmost."""
    return f"E{row}{reel}"


def configured_games(games_path: str | None = None) -> dict[str, dict]:
    """Every game in game_config.json with a `payline_geometry` block, keyed by process.

    Read off the file rather than a loaded `cfg`, because `cfg["game"]` is deliberately the only
    view of the games. This is for the two questions about the *file*: which games the error below
    should list, and the test modules' "does every shipped block validate".
    """
    path = games_path or DEFAULT_GAME_CONFIG
    try:
        with open(path, encoding="utf-8") as fh:
            games = (json.load(fh) or {}).get("games") or {}
    except (OSError, ValueError):
        return {}
    return {process: (block or {})[GEOMETRY_KEY]
            for process, block in games.items()
            if (block or {}).get(GEOMETRY_KEY)}


def geometry_for_game(process: str, games_path: str | None = None) -> Geometry:
    """One named game's geometry, whatever `active` currently says -- what the test modules and an
    audit of an old capture need. Same refusal: a process with no block raises."""
    blocks = configured_games(games_path)
    if process not in blocks:
        raise PaylineError(_no_block_message(process, games_path))
    return Geometry(process, blocks[process])


def _no_block_message(process: str, games_path: str | None = None) -> str:
    """The refusal, in one place -- both callers must say the same thing.

    It names the file it was read from rather than saying "game_config.json", because `--config`
    points at another folder's pair and the listing would then come from a file this run never read.
    """
    path = games_path or DEFAULT_GAME_CONFIG
    known = ", ".join(sorted(configured_games(path))) or "none"
    return (f"no payline reel geometry for {process}. A game's block carries it under "
            f"\"{GEOMETRY_KEY}\", and {path} sets it for: {known}. Reel positions are "
            f"fractions of the frame and survive a change of screen size but NOT a change of "
            f"game, so this will not fall back to another game's numbers -- measure this one "
            f"with `python -m server.payline.cli --profile <a spin_result.png> X0 Y0 X1 Y1` "
            f"(a rough box around the reels) and add a \"{GEOMETRY_KEY}\" block to "
            f"games[\"{process}\"]")


def geometry_for(cfg: dict) -> Geometry:
    """The geometry for the active game, off `cfg["game"]` -- where `extract` reads `meter_roi`.

    Raises and names the process when there is no block. There is deliberately no fallback: a grid
    read off the wrong fractions is a confident answer about pixels holding something else.
    """
    game = cfg.get("game") or {}
    process = game.get("process")
    if not process:
        raise PaylineError(
            "there is no active game, so there is no way to tell which game's reel "
            "geometry to use. Set \"active\" in game_config.json to the game's executable "
            "name, e.g. \"FortuneOx.exe\"")

    block = game.get(GEOMETRY_KEY)
    if not block:
        raise PaylineError(_no_block_message(process))

    return Geometry(process, block)
