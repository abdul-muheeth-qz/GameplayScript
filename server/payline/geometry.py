"""Where the reels are, and what a payline is -- the rule, over numbers a person edits.

**The numbers live in `game_config.json`**, one block per game beside that game's
`window_class`, `log`, `targets` and `meter_roi`:

    games["FortuneOx.exe"]["payline_geometry"] = {
        "label", "reels_roi", "reel_bounds", "row_bounds", "inner_margin_frac"
    }

They used to be a `GAMES` dict in this file, which meant adding a game was a code edit in one
place and a config edit in another -- the same split `settings.active_game` exists to close.
`settings.load_config` folds the active game's whole block onto `cfg["game"]`, so this stage
reads its geometry from exactly where `extract` reads `meter_roi` and `gameclick` reads
`targets`, and one `active` line still decides all of them. This module keeps the rule: what a
valid block is, what a payline is, and the refusal to guess.

Same idiom as `meter_roi` (see `extract/slotocr/roi.py`): every number is a **fraction**, never
a pixel. Three levels of it, because the thing being located is nested:

    reels_roi          fractions of the whole FRAME  -- where the reel window is
    reel/row_bounds    fractions of that ROI         -- where the 15 cells are inside it
    inner_margin_frac  fraction of each CELL         -- how much to trim off every side

That is what makes it work on a bigger screen. The same frame resampled from 0.6x to 3.0x
(648x1109 up to 3240x5547) read the identical grid and the identical five verdicts at every
size. A flat pixel margin would not have: 8 px is a fifth of a cell at 0.6x and a fortieth
at 3x, so it is expressed against the cell it trims.

**Keyed by the active game's process, and a game with no block raises.** Fractions survive a change
of *scale*; they do not survive a change of *aspect ratio* or of game art. FortuneOx's reel
window is 0.045-0.956 of the width and 0.564-0.827 of the height; applying that to
HuffNPuffLink's 612x961 portrait window lands on the wrong pixels entirely and would report
a confident grid read off whatever art happened to be there. `gameclick` learned this the
expensive way -- run 2026-08-12_131459 clicked HuffNPuffLink's TAKE WIN fraction on FortuneOx,
hit empty space and killed the capture -- so this follows the rest of the block's rule: **name
the process and refuse, never fall back to another game's numbers.**

**Where the shipped FortuneOx numbers came from**, since JSON cannot hold the comment that used
to sit beside them: measured off `server/captured_files/2026-08-12_124528/spin_result.png` at
1080x1849, and not converted from the POC's hardcoded pixels, which were taken on a 1073x1852
capture and sit ~7 px left of the reels here (and include a slice of the red frame). The reel
background is a flat purple no other part of the screen shares, so all four edges are
unambiguous -- 0.0% purple above y1042 and below y1528, none left of x49 or right of x1032 --
and the four gutters between reels are 8 px of non-purple at x239-246, 438-445, 636-643 and
835-842, which is where `reel_bounds`' gaps come from: the gold frame between reels is therefore
outside every cell rather than inside one. Rows have no gutter at all (the purple is continuous
top to bottom), so the even three-way split is not an approximation, it is the layout. The
fractions map back as:

    reel_bounds  px  49..238  247..437  446..635  644..834  843..1032
    row_bounds   px  1042..1203  1204..1366  1367..1528
    inner_margin_frac  0.0407 -- the POC's 8 px against the 196x162 cell it was trimming

To add a game, and this is the procedure those numbers came from:

    python -m server.payline.cli --profile <a spin_result.png> 0 900 1080 1600

Draw a rough box around the reels first (any image viewer will do) and hand it those four
numbers. It reports where the reel background actually begins and ends inside that box and
which columns hold almost none of it -- the gutters between reels. Divide the edges by the
frame's width and height for `reels_roi`, express the gutters as fractions of the ROI's width
for `reel_bounds`, and check the result on `payline/tiles/contact_sheet.png` before trusting
a single similarity number.

**It will not find the box for you, and that is not an omission.** Two auto-detection
approaches were written and measured against this cabinet's nine FortuneOx frames, and both
failed; `tiles.profile`'s docstring records how, so they are not retried. A wrong crop here
does not crash -- it reads a confident grid off the wrong pixels -- which is worth a person's
minute.
"""

from __future__ import annotations

import json

from ..settings import DEFAULT_GAME_CONFIG


class PaylineError(Exception):
    """Something went wrong in a way the user can act on."""


# The Payline.xlsx rule set: five lines over a 3x5 grid, cells named E{row}{reel} with
# row 1 at the top and reel 1 at the left. A game whose paytable differs sets its own
# `paylines` in its block below; these are the default because they are the spec the
# logic in paylines.py was written and tested against.
DEFAULT_PAYLINES = [
    {"id": 1, "name": "Middle row", "cells": ["E21", "E22", "E23", "E24", "E25"]},
    {"id": 2, "name": "Top row", "cells": ["E11", "E12", "E13", "E14", "E15"]},
    {"id": 3, "name": "Bottom row", "cells": ["E31", "E32", "E33", "E34", "E35"]},
    {"id": 4, "name": "V shape", "cells": ["E11", "E22", "E33", "E24", "E15"]},
    {"id": 5, "name": "Inverted V shape", "cells": ["E31", "E22", "E13", "E24", "E35"]},
]


# The key a game's block carries its reel geometry under, in game_config.json.
GEOMETRY_KEY = "payline_geometry"

# The three a block cannot be read without. `label` and `inner_margin_frac` have defaults;
# these do not, because there is nothing safe to default them to -- see `geometry_for`.
REQUIRED_KEYS = ("reels_roi", "reel_bounds", "row_bounds")


class Geometry:
    """One game's reel geometry, validated. Read-only after construction."""

    def __init__(self, process: str, block: dict):
        self.process = process
        self.label = block.get("label", "unnamed")
        self.notes = block.get("notes", "")
        self.measured_on = block.get("measured_on")

        # The block is hand-edited JSON now rather than a literal in this file, so a missing
        # key or a quoted number arrives here at runtime instead of being a typo caught while
        # writing it. Both are named against the key to fix, for the same reason _validate
        # exists: the alternative is a KeyError or a TypeError on the browser's screen.
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

        self.paylines = block.get("paylines") or DEFAULT_PAYLINES
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

        `geometry.pixel_box` clamps to the image, so a fraction above 1.0 or a pair the
        wrong way round does not raise on its own -- it quietly returns a crop of the
        frame's edge, and every meter reads as an unrecognisable symbol with nothing to
        say why. Named here instead, against the key to fix.
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
    """Every game in game_config.json that has a `payline_geometry` block, keyed by process.

    Read straight off the file rather than out of a loaded `cfg`, because `cfg["game"]` is
    deliberately the *only* view of the games there (`settings.py`: an unresolved
    `cfg["games"]` was published once, for `extract`'s box race, and went with it). So this
    is for the two questions that are about the file and not about the running game: which
    games the error message below should list, and the test modules' "does every shipped
    block validate".
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
    """One named game's geometry, whatever `active` currently says.

    `geometry_for` is what the stages call and it answers for the game that is *running*.
    This names the game instead, which is what a test checking the shipped FortuneOx block
    needs -- and what auditing an old capture of a game that is not the one running now
    would need. It is still the same refusal: a process with no block raises.
    """
    blocks = configured_games(games_path)
    if process not in blocks:
        raise PaylineError(_no_block_message(process, games_path))
    return Geometry(process, blocks[process])


def _no_block_message(process: str, games_path: str | None = None) -> str:
    """The refusal, in one place -- both callers must say the same thing.

    The listing names the file it was read from rather than saying "game_config.json", because
    `--config` points `load_config` at another folder's pair: a bare "it is set for: X" would
    then be a list from a file this run never read.
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
    """The geometry for the active game -- `game_config.json`'s `active`.

    The block comes off `cfg["game"]`, which `settings.active_game` has already resolved and
    folded the whole of the active game's block onto -- so this reads `payline_geometry` from
    exactly where `extract` reads `meter_roi`, and one `active` line decides both.

    Raises and names the process when there is no block for it. There is deliberately no
    fallback: reading a grid off the wrong fractions produces a confident answer about
    pixels that hold something else, which is worse than no answer. See the module
    docstring for the run that established that.
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
