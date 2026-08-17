"""The reel strips: which symbol each reel shows at each stop, read out of a spreadsheet.

One sheet: a `Position` column and one column per reel, 200 positions of symbol names each. A
reel showing stop `s` shows positions s, s+1, s+2, so cell `E{row}{reel}` is
`strip[reel][stop + row - 1]` with row 1 at the top.

**That rule was measured over two spins and 30 cells**, both 15/15 against their own contact sheets.
Two things about the data are load-bearing:

  * **Position 200 is `X` in every reel: a terminator, not a symbol.** The strips are 200 long and
    that is the modulus for the wrap -- a strip of 201 would hand out `X` as a symbol name.
  * **The mystery symbols do not say what is on the screen.** They are 15% of every strip and reveal
    as other art, so a pair involving one can never be decided by name (`placeholders`). `WILD` is
    deliberately *not* in that set -- it has its own art and was drawn as itself on both frames.

**The sheet is a per-game asset and is named in the game's own block**
(`games.<exe>.reel_strips`), not by a cabinet-level setting and not by a module default. This is
the reel layout of one particular game: it was `DEFAULT_STRIPS = "assets/payline_excel.xlsx"` here
with a `payline.reel_stops.strips` override in config.json, which meant `active` switched the reel
geometry, the meter box, the click points and the log while leaving the spreadsheet behind. Measured
on this machine before the change: with a `payline_geometry` block added for HuffNPuffLink -- whose
log is the one that actually carries `reelsStops` here -- the checkpoint read its real stops
`[68, 18, 43, 51, 1]`, mapped them through *FortuneOx's* sheet, and reported `status: "on"` with a
confident grid of Ace/Mystery1/King. Nothing said the two came from different games, because
nothing knew.

`placeholders` is required alongside the path rather than defaulting to the names above, and the
direction of the failure is why: a placeholder set that is too *large* only makes the checkpoint
abstain and the pixels decide, while one that is too *small* lets it settle a COMPARE on a name the
screen is not showing. An empty list is a statement; an absent key is an omission, so it is refused.

One measured disagreement is on the record, and is why the checkpoint reports every adjudication
rather than just applying it: on one frame the sheet has `Wealth Pot` where the pixels show an Ox,
1 cell in 30. It is also why nothing here may overturn a *confident* pixel reading.

Parsed with `zipfile` and `xml.etree` rather than openpyxl -- an .xlsx is a zip of XML, and the
alternative is a dependency for one file of one sheet. Note the symbol names are cached XLOOKUP
results against an external workbook, so a reader handling only shared and inline strings finds an
empty sheet, which is what the first version of this did.
"""

from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
import zipfile

from ..settings import resolve
from .geometry import PaylineError, cell_name

LOG = logging.getLogger("payline")

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# The key a game's block carries its reel strips under, in game_config.json.
STRIPS_KEY = "reel_strips"

# Row 1 is the title and row 2 the R1..R5 header, so position 0 is B3. Both describe the *sheet's*
# layout rather than a game's reels, and a game whose sheet is laid out differently overrides them
# with `columns` and `first_row`; the reel *count* is checked against the geometry by `grid`.
FIRST_DATA_ROW = 3
REEL_COLUMNS = ("B", "C", "D", "E", "F")

# The end-of-strip marker, not a symbol.
TERMINATOR = "X"


def normalize_placeholders(names) -> frozenset[str]:
    """The placeholder names as they are compared: stripped and upper-cased, once, here."""
    return frozenset(str(n).strip().upper() for n in (names or ()) if str(n).strip())


class ReelStrips:
    """One game's reel layout: the symbol at every position of every reel.

    Carries its own `placeholders`, because they are a fact about this sheet's symbol names and
    travel with it -- a module-level set was one more per-game value that a change of `active` did
    not move.
    """

    def __init__(self, strips: dict[int, list[str]], source: str, placeholders=()):
        self.strips = strips
        self.source = source
        self.placeholders = normalize_placeholders(placeholders)

    def is_placeholder(self, symbol: str | None) -> bool:
        """Is this a name the sheet knows but the screen may not be showing?"""
        return bool(symbol) and symbol.strip().upper() in self.placeholders

    @property
    def reels(self) -> int:
        return len(self.strips)

    def lengths(self) -> dict[int, int]:
        return {reel: len(s) for reel, s in self.strips.items()}

    def symbol(self, reel: int, stop: int, row: int) -> str:
        """The symbol reel `reel` shows in `row` when it stopped at `stop`.

        Rows are 1-based from the top, and the strip wraps -- a stop of 199 shows 199, 0, 1.
        """
        strip = self.strips.get(reel)
        if not strip:
            raise PaylineError(
                f"{self.source} has no column for reel {reel}. The sheet needs one column "
                f"per reel ({', '.join(REEL_COLUMNS)}) beside the Position column")
        return strip[(int(stop) + row - 1) % len(strip)]

    def grid(self, stops, rows: int, reels: int) -> dict[str, str]:
        """`{'E11': 'Pisces', ...}` for one spin's stops. `stops` is one per reel, left first."""
        if len(stops) < reels:
            raise PaylineError(
                f"the game log gave {len(stops)} reel stops but this game's geometry has "
                f"{reels} reels, so there is no stop for reel {len(stops) + 1}. Check that "
                f"games.<exe>.log in game_config.json names the running game's log")
        return {cell_name(r, c): self.symbol(c, stops[c - 1], r)
                for r in range(1, rows + 1)
                for c in range(1, reels + 1)}


# ---------------------------------------------------------------------------
# reading the spreadsheet
# ---------------------------------------------------------------------------

def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    """The workbook's shared string table, or [] when it has none."""
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    return ["".join(t.text or "" for t in si.iter(f"{NS}t"))
            for si in ET.fromstring(raw).findall(f"{NS}si")]


def _cell_text(cell, shared: list[str]) -> str | None:
    """One cell as text, in any of the encodings this sheet uses.

    The symbol names are `t="str"` -- cached XLOOKUP results against an external workbook -- so the
    shared string table holds only the headers and the names live in each cell's own `<v>`. The
    consequence is that these are the values Excel last calculated: re-saving the sheet somewhere
    that cannot reach that workbook yields blanks, which `load_strips` catches by raising on an
    empty column.
    """
    kind = cell.get("t")
    if kind == "s":                                    # shared string
        value = cell.find(f"{NS}v")
        if value is None or value.text is None:
            return None
        index = int(value.text)
        if index >= len(shared):
            return None
        return shared[index]
    if kind == "inlineStr":                            # <is><t>text</t></is>
        return "".join(t.text or "" for t in cell.iter(f"{NS}t")) or None
    # A formula result (t="str"), a number, a date, a boolean -- and the `<t>` fallback for
    # anything that spells its text out instead of putting it in `<v>`.
    value = cell.find(f"{NS}v")
    if value is not None and value.text is not None:
        return value.text
    return "".join(t.text or "" for t in cell.iter(f"{NS}t")) or None


def _column_of(ref: str) -> str:
    """'C17' -> 'C'."""
    return "".join(ch for ch in ref if ch.isalpha())


def _sheet_cells(path: str) -> dict[tuple[int, str], str]:
    """`{(row, 'B'): text}` for the workbook's first worksheet."""
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise PaylineError(
            f"{path} could not be read as a spreadsheet ({exc}). games.<exe>.{STRIPS_KEY}.path "
            f"must point at an .xlsx -- an .xls or a CSV saved with that extension will not "
            f"open") from None

    with archive:
        names = [n for n in archive.namelist() if n.startswith("xl/worksheets/sheet")]
        if not names:
            raise PaylineError(f"{path} has no worksheets in it")
        shared = _shared_strings(archive)
        sheet = ET.fromstring(archive.read(sorted(names)[0]))

    data = sheet.find(f"{NS}sheetData")
    if data is None:
        raise PaylineError(f"{path}: the first worksheet has no rows")

    cells = {}
    for row in data.findall(f"{NS}row"):
        for cell in row.findall(f"{NS}c"):
            ref = cell.get("r") or ""
            text = _cell_text(cell, shared)
            if text is not None:
                cells[(int(row.get("r")), _column_of(ref))] = text.strip()
    return cells


def strips_for(cfg: dict) -> ReelStrips:
    """The active game's reel strips, out of its own block (`cfg["game"]`).

    Raises `PaylineError` naming the key when the game has no `reel_strips`, and **never falls back
    to another game's sheet** -- see the module docstring for the measured cross-game grid that
    fallback produced. `matcher.build_checkpoint` catches that and reports `status: "unavailable"`,
    so a game with no strips is audited on the pixels alone, which is the correct answer rather than
    a degraded one.
    """
    game = (cfg or {}).get("game") or {}
    process = game.get("process")
    if not process:
        raise PaylineError(
            "there is no active game, so there is no way to tell whose reel strips to read. "
            "Set \"active\" in game_config.json to the running game's executable name")

    block = game.get(STRIPS_KEY)
    if not block:
        raise PaylineError(
            f"{process} has no \"{STRIPS_KEY}\" in game_config.json, so this spin's reel stops "
            f"cannot be turned into symbol names and the reel-stop checkpoint cannot run -- the "
            f"audit falls back to the pixels alone. Add \"{STRIPS_KEY}\": {{\"path\": "
            f"\"assets/<this game>.xlsx\", \"placeholders\": [...]}} to games.{process}. A reel "
            f"strip is one game's symbol layout, so this will not read another game's sheet: it "
            f"would name the symbols of a spin that never happened")
    if not isinstance(block, dict):
        raise PaylineError(
            f"games.{process}.{STRIPS_KEY} is {block!r}; it must be an object with \"path\" and "
            f"\"placeholders\" keys")

    path = block.get("path")
    if not path:
        raise PaylineError(f"games.{process}.{STRIPS_KEY} has no \"path\", so there is no "
                           f"spreadsheet to read the reel layout from")
    if "placeholders" not in block:
        raise PaylineError(
            f"games.{process}.{STRIPS_KEY} has no \"placeholders\", so there is no way to tell "
            f"which of this sheet's names are mystery symbols that reveal as other art. Set it to "
            f"the list of those names, or to [] if this game has none -- an empty list is a "
            f"statement, and a missing key is only an omission, so it is not assumed")

    return load_strips(path, block["placeholders"], block.get("columns"),
                       block.get("first_row", FIRST_DATA_ROW))


def load_strips(path: str, placeholders=(), columns=None,
                first_row: int = FIRST_DATA_ROW) -> ReelStrips:
    """Read one game's reel layout out of its spreadsheet.

    Down each reel's column from `first_row` until it runs out or hits the `X` terminator, so
    the strips are exactly as long as the sheet says and nothing invents a symbol past the end.
    """
    columns = tuple(columns or REEL_COLUMNS)
    # Anchored on `server/`, never the CWD: a server started elsewhere must find the same sheet the
    # CLI does.
    target = resolve(path)
    if not os.path.isfile(target):
        raise PaylineError(
            f"the reel strip spreadsheet {target} does not exist. games.<exe>.{STRIPS_KEY}.path "
            f"points at it; set it to the sheet's path, or set payline.reel_stops.enabled to "
            f"false to audit on the pixels alone")

    cells = _sheet_cells(target)
    strips: dict[int, list[str]] = {}
    for index, column in enumerate(columns, start=1):
        strip: list[str] = []
        row = first_row
        while True:
            value = cells.get((row, column))
            if value is None or value.upper() == TERMINATOR:
                break
            strip.append(value)
            row += 1
        if strip:
            strips[index] = strip

    if not strips:
        raise PaylineError(
            f"{target} has no reel strips in it. Expected one column per reel "
            f"({', '.join(columns)}) with symbol names from row {first_row} down, "
            f"beside a Position column")

    lengths = {reel: len(s) for reel, s in strips.items()}
    if len(set(lengths.values())) > 1:
        # Not fatal -- each reel wraps on its own length -- but worth saying, because a column that
        # stops early reads as a shorter reel rather than as an error.
        LOG.warning("reel strips are not all the same length: %s", lengths)
    LOG.info("read %d reel strips from %s (%s positions each)",
             len(strips), target, sorted(set(lengths.values())))
    return ReelStrips(strips, target, placeholders)
