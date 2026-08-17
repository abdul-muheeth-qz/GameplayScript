"""THE CHECKPOINT, part 1 -- the reel strips, read out of the spreadsheet.

`server/assets/payline_excel.xlsx` is one sheet: a `Position` column and one column per reel
(`R1`..`R5`), 200 positions of symbol names each. A spin's `reelsStops` from the game's own
log is one stop per reel, and the three cells that reel shows are the strip entries
at **stop, stop+1, stop+2** -- so cell `E{row}{reel}` is `strip[reel][stop[reel] + row - 1]`,
row 1 at the top.

**That rule was measured, not assumed.** Two spins, 30 cells:

  * The mapping supplied with the request -- stops `[24, 79, 153, 25, 0]` -- reproduces all
    fifteen names exactly (Pisces/King/Mystery1/Ox/Arm Band across the top row, down to
    Arm Band/Arm Band/Pisces/Wealth Pot/Orb (Splittable) across the bottom).
  * Run `2026-08-13_153618`, stops `[86, 121, 127, 138, 86]`, is **15/15** against its own
    `payline/tiles/contact_sheet.png` -- five Arm Bands across the middle row, Free Games at
    E32, Q at E33, A at E34.

Two things about the data itself, both load-bearing:

  * **Position 200 is `X` in every reel and is a terminator, not a symbol.** The strips are
    therefore 200 long (0..199) and that is the modulus for the wrap, which only matters for
    a stop of 198 or 199 -- exactly the case where getting it wrong is invisible, because a
    strip of 201 would hand out `X` as a symbol name and `X` matches nothing.
  * **`Mystery1`, `Mystery2` and `Mystery (Orb)` do not say what is on the screen.** They are
    15% of every strip, and they reveal as another symbol: the supplied example has `Mystery1`
    at E13 where the frame shows an Ace, and run `2026-08-13_155048` has `Mystery1` on all
    three cells of reel 1 where the frame shows three Ox. So a mystery name can never be
    compared against another name -- see `PLACEHOLDERS` and `matcher.ReelStopMatcher`, which
    abstains rather than guessing. `WILD` is *not* in that set: it has its own art (the
    firecrackers) and was drawn as itself on both frames checked.

One measured disagreement is on the record and is the reason the checkpoint reports every
adjudication instead of just applying it. Run `2026-08-13_155048`, stops
`[135, 89, 15, 144, 75]`, is 14/15: the sheet has `Wealth Pot` at R2 position 89 and the frame
shows an Ox at E12. Every other cell on that frame agrees, including the three revealed
mysteries beside it, so this is either strip drift between the sheet and the build or a reveal
that reached further than reel 1. It is 1 cell in 30 measured, and it is why nothing here is
allowed to overturn a *confident* pixel reading -- only the ambiguous band.

Parsed with `zipfile` and `xml.etree`, not openpyxl: an .xlsx is a zip of XML, this sheet is
plain shared strings, and the alternative is a dependency in `requirements.txt` for one file
of one sheet. The parser handles the three cell encodings Excel actually writes here --
shared-string, inline string and bare value -- and raises with the file named on anything else.
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

DEFAULT_STRIPS = "server/assets/payline_excel.xlsx"

# The row the strips start on (row 1 is the "Reel Layout - 1" title, row 2 the R1..R5 header)
# and the column the first reel is in. Position 0 is therefore B3.
FIRST_DATA_ROW = 3
REEL_COLUMNS = ("B", "C", "D", "E", "F")

# The end-of-strip marker in the sheet, not a symbol. See the module docstring.
TERMINATOR = "X"

# Names that do not describe what is drawn on the screen, because the symbol reveals as
# something else. A pair involving one of these cannot be decided by name.
PLACEHOLDERS = frozenset({"MYSTERY1", "MYSTERY2", "MYSTERY (ORB)"})


def is_placeholder(symbol: str | None) -> bool:
    """Is this a name the sheet knows but the screen may not be showing?"""
    return bool(symbol) and symbol.strip().upper() in PLACEHOLDERS


class ReelStrips:
    """One reel layout: the symbol at every position of every reel."""

    def __init__(self, strips: dict[int, list[str]], source: str):
        self.strips = strips
        self.source = source

    @property
    def reels(self) -> int:
        return len(self.strips)

    def lengths(self) -> dict[int, int]:
        return {reel: len(s) for reel, s in self.strips.items()}

    def symbol(self, reel: int, stop: int, row: int) -> str:
        """The symbol reel `reel` shows in `row` when it stopped at `stop`.

        Rows are 1-based from the top, which is `cell_name`'s convention, and the strip
        wraps -- a stop of 199 shows positions 199, 0, 1.
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

    **The symbol names are `t="str"` -- cached *formula results*, not shared strings.** Every
    one of them is an `XLOOKUP` against an external workbook (`xl/externalLinks/`), so the
    sheet's shared string table holds only seven entries -- the title, `Position` and `R1`..`R5`
    -- and the 1000 names live in each cell's own `<v>`. A reader that handles only shared and
    inline strings finds an empty sheet, which is what the first version of this did.

    The practical consequence is that these are the values Excel last calculated. They are
    correct as shipped and verified against two spins, but re-saving the sheet somewhere that
    cannot reach the external workbook is a way to get blanks, and `load_strips` raising on an
    empty column is the thing that would catch it.
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
            f"{path} could not be read as a spreadsheet ({exc}). payline.reel_stops.strips "
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


def load_strips(path: str | None = None) -> ReelStrips:
    """Read the reel layout out of the spreadsheet.

    Reads down each reel's column from `FIRST_DATA_ROW` until the column runs out or hits
    the `X` terminator, so the strips are exactly as long as the sheet says and nothing
    invents a symbol past the end.
    """
    # Anchored on the repository root, never on the CWD: a server started from somewhere else
    # has to find the same spreadsheet the CLI does. `settings_for` resolves the configured
    # path the same way, so this only matters for the default and for a direct call.
    target = resolve(path or DEFAULT_STRIPS)
    if not os.path.isfile(target):
        raise PaylineError(
            f"the reel strip spreadsheet {target} does not exist. payline.reel_stops.strips "
            f"points at it; set it to the sheet's path, or set payline.reel_stops.enabled to "
            f"false to audit on the pixels alone")

    cells = _sheet_cells(target)
    strips: dict[int, list[str]] = {}
    for index, column in enumerate(REEL_COLUMNS, start=1):
        strip: list[str] = []
        row = FIRST_DATA_ROW
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
            f"({', '.join(REEL_COLUMNS)}) with symbol names from row {FIRST_DATA_ROW} down, "
            f"beside a Position column")

    lengths = {reel: len(s) for reel, s in strips.items()}
    if len(set(lengths.values())) > 1:
        # Not fatal -- each reel wraps on its own length -- but it is worth saying out loud,
        # because a column that stops early reads as a shorter reel rather than as an error.
        LOG.warning("reel strips are not all the same length: %s", lengths)
    LOG.info("read %d reel strips from %s (%s positions each)",
             len(strips), target, sorted(set(lengths.values())))
    return ReelStrips(strips, target)
