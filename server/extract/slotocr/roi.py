"""Crop a full screenshot to the CASH/WIN/BET meter bar.

One box, the active game's `games.<exe>.meter_roi`, cropped through `server.utils.crop_roi`.
Nothing is searched for, nothing is scored, and there is no second box behind it -- a crop that
misses shows up as null meters, which is the failure worth having.
"""
import logging
from typing import NamedTuple

from ...utils import RoiCropError, crop_roi

LOG = logging.getLogger("extract")


class MeterROI(NamedTuple):
    """The meter-bar crop, and `config:<exe>` naming the box it was cropped to.

    `source` rides out in the record rather than only being logged: it is the fastest way to tell
    a mis-tuned ROI from a bad OCR read.
    """
    image: object
    source: str


def locate_meter_roi(image, cfg: dict | None = None) -> MeterROI:
    """Crop `image` to the active game's `meter_roi`.

    Raises `RoiCropError` naming what to fix when there is no active game, no `meter_roi` on it,
    or the box is not a region of this image. Never falls back to another game's box.
    """
    game = (cfg or {}).get("game") or {}
    process = game.get("process")
    if not process:
        raise RoiCropError(
            "there is no active game, so there is no meter_roi to crop the meter strip "
            "to. Set \"active\" in game_config.json to the running game's executable "
            "name, e.g. \"HuffNPuffLink.exe\"")

    box = game.get("meter_roi")
    if not box:
        raise RoiCropError(
            f"{process} has no \"meter_roi\" in game_config.json, so there is no box to "
            f"crop the meter strip to. Add \"meter_roi\": [x0, y0, x1, y1] to "
            f"games.{process} -- normalized fractions of the frame, the way \"targets\" "
            f"are. This will not fall back to another game's box: reel and meter "
            f"positions survive a change of screen size but not a change of game")

    crop = crop_roi(image, box, what=f"the meter ROI for {process} (games.{process}."
                                     f"meter_roi in game_config.json)")
    LOG.debug("cropped the meter strip to %s's meter_roi %s", process, list(box))
    return MeterROI(crop, f"config:{process}")
