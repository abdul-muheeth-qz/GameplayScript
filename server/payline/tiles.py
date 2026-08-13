"""STEP 1a - crop the reels window out of the frame and cut it into cells.

    payline/reels.png            the cropped reel window
    payline/tiles/e11.png ...    one file per cell, lowercase names
    payline/tiles/contact_sheet.png   all cells on one image, labelled

**Look at the contact sheet before trusting a single similarity number.** Every number this
stage feeds downstream is meaningless if the crop is half a cell out, and the sheet shows
that in one glance -- which is why the tiles are their own step in the UI rather than an
invisible part of the reading.

The crop resolves through `server.geometry.pixel_box`, the same rule `extract` crops the
meter strip with. Two levels of it: the ROI is a fraction of the frame, and each cell is a
fraction of the ROI. The inner margin is a fraction of the cell, not a pixel count, so a
bigger screen trims proportionally rather than shaving a sliver off a much larger tile.
"""

from __future__ import annotations

import logging
import os

from PIL import Image, ImageDraw

from ..geometry import pixel_box
from .geometry import PaylineError, cell_name

LOG = logging.getLogger("payline")

REELS_FILE = "reels.png"
TILES_SUBDIR = "tiles"
CONTACT_SHEET = "contact_sheet.png"


def crop_reels(image_path: str, geometry) -> Image.Image:
    """Open a frame and return just the reel window."""
    try:
        frame = Image.open(image_path).convert("RGB")
    except OSError as exc:
        raise PaylineError(f"cannot read {image_path}: {exc}") from None

    edges = pixel_box(geometry.reels_roi, frame.width, frame.height)
    if edges is None:
        raise PaylineError(
            f"the reels ROI {geometry.reels_roi} is empty on a "
            f"{frame.width}x{frame.height} frame -- check reels_roi for "
            f"{geometry.process} in server/payline/geometry.py")
    return frame.crop(edges)


def cell_boxes(geometry, roi_size: tuple[int, int]) -> dict[str, tuple[int, int, int, int]]:
    """{'E11': (x0, y0, x1, y1), ...} in pixels within an ROI of `roi_size`.

    The margin is already trimmed off every side. Edges come from the fractions
    independently rather than from a per-cell pixel width, so a cell's right edge lands on
    exactly the pixel the gutter beside it starts at.
    """
    roi_w, roi_h = roi_size
    boxes = {}
    for r, (fy0, fy1) in enumerate(geometry.row_bounds, start=1):
        for c, (fx0, fx1) in enumerate(geometry.reel_bounds, start=1):
            x0, x1 = fx0 * roi_w, fx1 * roi_w
            y0, y1 = fy0 * roi_h, fy1 * roi_h
            margin_x = (x1 - x0) * geometry.inner_margin_frac
            margin_y = (y1 - y0) * geometry.inner_margin_frac
            box = (round(x0 + margin_x), round(y0 + margin_y),
                   round(x1 - margin_x), round(y1 - margin_y))
            if box[2] <= box[0] or box[3] <= box[1]:
                raise PaylineError(
                    f"cell {cell_name(r, c)} is empty on a {roi_w}x{roi_h} reel window. "
                    f"The frame is too small for this geometry, or inner_margin_frac "
                    f"({geometry.inner_margin_frac}) is trimming the whole cell away")
            boxes[cell_name(r, c)] = box
    return boxes


def build_tiles(image_path: str, geometry, out_dir: str | None = None,
                save: bool = True) -> tuple[dict[str, Image.Image], Image.Image, dict]:
    """Crop the reel window, cut the cells, optionally save them.

    Returns `(tiles, reels, info)` -- the cells by name, the reel window itself, and what
    the record should say about this crop.
    """
    reels = crop_reels(image_path, geometry)
    boxes = cell_boxes(geometry, reels.size)
    tiles = {name: reels.crop(box) for name, box in boxes.items()}

    any_box = next(iter(boxes.values()))
    info = {
        "image": os.path.basename(image_path),
        "frame_size": None,          # filled by the caller, which knows the whole frame
        "reels_size": f"{reels.width}x{reels.height}",
        "tile_size": f"{any_box[2] - any_box[0]}x{any_box[3] - any_box[1]}",
        "cells": len(tiles),
        "geometry": geometry.describe(),
        "files": {},
    }

    if save and out_dir:
        os.makedirs(out_dir, exist_ok=True)
        reels.save(os.path.join(out_dir, REELS_FILE))
        tiles_dir = os.path.join(out_dir, TILES_SUBDIR)
        os.makedirs(tiles_dir, exist_ok=True)
        for name, tile in tiles.items():
            tile.save(os.path.join(tiles_dir, f"{name.lower()}.png"))
        contact_sheet(geometry, tiles).save(os.path.join(tiles_dir, CONTACT_SHEET))
        info["files"] = {
            "reels": REELS_FILE,
            "contact_sheet": f"{TILES_SUBDIR}/{CONTACT_SHEET}",
            "tiles": {name: f"{TILES_SUBDIR}/{name.lower()}.png" for name in tiles},
        }
        LOG.info("cut %d tiles from %s into %s", len(tiles), info["image"], out_dir)

    return tiles, reels, info


def contact_sheet(geometry, tiles, thumb=140, pad=8) -> Image.Image:
    """Every cell on one labelled image -- the fastest way to spot a bad crop."""
    cols, rows = geometry.reels, geometry.rows
    label_h = 18
    sheet = Image.new(
        "RGB",
        (cols * thumb + (cols + 1) * pad, rows * (thumb + label_h) + (rows + 1) * pad),
        (18, 18, 22),
    )
    draw = ImageDraw.Draw(sheet)
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            name = cell_name(r, c)
            x = pad + (c - 1) * (thumb + pad)
            y = pad + (r - 1) * (thumb + label_h + pad)
            sheet.paste(tiles[name].resize((thumb, thumb)), (x, y))
            draw.text((x + 2, y + thumb + 3), name, fill=(230, 230, 230))
    return sheet


def load_tiles(out_dir: str, geometry) -> dict[str, Image.Image] | None:
    """The tiles a previous run of this stage saved, or None if they are not all there."""
    tiles_dir = os.path.join(out_dir, TILES_SUBDIR)
    tiles = {}
    for name in geometry.cells:
        path = os.path.join(tiles_dir, f"{name.lower()}.png")
        if not os.path.isfile(path):
            return None
        tiles[name] = Image.open(path).convert("RGB")
    return tiles


# ---------------------------------------------------------------------------
# measuring a new game
# ---------------------------------------------------------------------------

def profile(image_path: str, x0: int, y0: int, x1: int, y1: int,
            sparse: float = 0.10) -> dict:
    """Reel-background density along both axes of a region -- the measuring aid for a new game.

    Given a rough box around the reels (read off any image viewer), this reports where the
    background actually starts and stops, and which columns inside it hold almost none --
    which on a game with gutters between the reels is exactly the reel boundaries. That is
    the measurement the shipped FortuneOx block came from, and it is a *reporting* tool: it
    prints profiles for a person to read, and does not decide anything.

    **It deliberately does not try to find the box itself.** Two attempts at that were
    written and both failed on this cabinet's own frames, in ways worth recording so they are
    not retried:

      * Thresholding row density (rows more than half background) collapses on large symbol
        art. The J's leave a row 70% background; a row of pots and fish leaves it under 50%,
        so the detected window shrank to a 39 px sliver of the 487 px it should be -- and
        loosening the threshold instead swept in the purple UI chrome above and below the
        reels and reported the window as nearly the whole screen.
      * Connected components over the background mask, with a morphological close to stop
        symbol art splitting a reel in two, merged all five reels into one blob: the close
        that bridges a symbol also bridges an 8 px gutter, and there is no kernel that does
        one without the other.

    A wrong crop here is not a crash, it is a confident grid read off the wrong pixels, so
    the numbers are worth a person's minute. Read the profile, take the edges, divide by the
    frame size, check the contact sheet.
    """
    import numpy as np

    frame = np.asarray(Image.open(image_path).convert("RGB")).astype(int)
    height, width, _ = frame.shape
    red, green, blue = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]

    # The reel background is the saturated non-grey field the symbols sit on. Purple here;
    # the test is "clearly coloured, and neither the bright frame art nor near-black". Edit
    # it for a game whose reels sit on something else.
    background = ((blue > 55) & (green < 75) & (blue > green + 35)
                  & (red > green + 5) & (red < 150))

    x0, x1 = max(0, x0), min(width, x1)
    y0, y1 = max(0, y0), min(height, y1)
    if x1 <= x0 or y1 <= y0:
        raise PaylineError(f"the region {(x0, y0, x1, y1)} is empty on a "
                           f"{width}x{height} frame")

    region = background[y0:y1, x0:x1]
    rows = region.sum(axis=1) / (x1 - x0)
    cols = region.sum(axis=0) / (y1 - y0)

    def edges(fractions, offset):
        lit = [i for i, f in enumerate(fractions) if f > sparse]
        return (offset + lit[0], offset + lit[-1] + 1) if lit else None

    gutters, start = [], None
    for i, fraction in enumerate(cols):
        if fraction <= sparse and start is None:
            start = i
        elif fraction > sparse and start is not None:
            gutters.append((x0 + start, x0 + i))
            start = None

    vertical = edges(rows, y0)
    horizontal = edges(cols, x0)
    return {
        "frame": f"{width}x{height}",
        "searched": (x0, y0, x1, y1),
        # Where the background begins and ends inside the region searched. These are the
        # reels_roi edges, if the region was drawn generously enough around them.
        "background_rows": vertical,
        "background_cols": horizontal,
        # Interior runs holding almost no background: the gutters between reels, plus the
        # margins if the region was drawn wider than the reels.
        "sparse_col_runs": gutters,
        "row_density": [round(float(f), 3) for f in rows],
        "col_density": [round(float(f), 3) for f in cols],
        "as_fractions": (None if not (vertical and horizontal) else
                        [round(horizontal[0] / width, 6), round(vertical[0] / height, 6),
                         round(horizontal[1] / width, 6), round(vertical[1] / height, 6)]),
    }
