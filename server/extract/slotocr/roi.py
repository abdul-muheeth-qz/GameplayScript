"""
Crop a full screenshot to the CASH/WIN/BET meter bar, so the rest of the
pipeline (and every OCR call) works on a small crop instead of the whole,
visually busy image.

**One box: the active game's.** `game_config.json`'s `active` names the running
game, `settings.load_config` resolves that game's block onto `cfg["game"]`, and
`games.<exe>.meter_roi` is the normalized `[x0, y0, x1, y1]` this crops to.
Nothing is searched for, nothing is scored, and there is no second box behind
it. Cropping itself is `server.utils.crop_roi`, shared with `payline` -- the
fraction-to-pixel rule has one definition and both stages call it.

**The race that used to be here is gone.** Every game's `meter_roi` was cropped
in turn, each candidate validated by running the real extraction over it and
ranked by how many meter fields came back, and the best-reading crop won
(`utils.crop_best_box`, with `roi.score_crop` and `_fields_resolved` supplying
the scoring). Three things were wrong with it and the first is the one that
matters:

  * **It chose which pixels to believe by reading them.** A box aimed at another
    game's layout lands somewhere unrelated on this screenshot and can still
    scrape one plausible number out of it, so "which pixels was this value read
    from?" was a question only `roi_source` could answer afterwards. Worse, the
    ranking was a field *count*, and counting fields is the exact thing the
    band sweep proved you must not tune a crop on -- the geometry that resolved
    the most fields there read a $1,089.00 balance as 108900.00 and invented a
    win of 89.00 out of the fragment ",089.00".
  * It cost a full extraction per candidate, ~8 s a frame each, to pick a box
    that config already knew. One box means one extraction, so this stage is now
    about twice as fast per frame on a two-game config.
  * It was the one place reading `cfg["games"]` (every game) rather than
    `cfg["game"]` (the active one), which made it the only stage whose answer
    did not depend on `active` -- so a mis-set `active` was invisible here and
    fatal everywhere else.

The cost, and it is a real one: a screenshot of a layout that is not the active
game's no longer reads. That is what `active` is for, and it is now the single
switch -- set it to the game a frame came from. See `server/extract/README.md`
for what that means for the `Images/` fixtures.

This module sits ABOVE extraction, which is why it is a separate module rather
than living in panel_detection -- extraction.py already depends on
panel_detection.py, and this used to import extraction to score a crop. It no
longer imports it at all.
"""
import logging
from typing import NamedTuple

from ...utils import RoiCropError, crop_roi

LOG = logging.getLogger("extract")

CONFIG_KEY = "game_config.json's games.<exe>.meter_roi"


class MeterROI(NamedTuple):
    """The meter-bar crop and which game's box it was cropped to.

    `source` is `config:<exe>` and rides out in the record rather than only
    being logged, because it is the fastest way to tell a mis-tuned ROI from a
    bad OCR read when a value comes back wrong.
    """
    image: object
    source: str


def locate_meter_roi(image, cfg: dict | None = None) -> MeterROI:
    """Crop `image` to the active game's `meter_roi`.

    Raises `RoiCropError`, naming what to fix, when there is no active game, when
    that game defines no `meter_roi`, or when the box is not a region of this
    image. There is deliberately no fallback to another game's box: normalized
    fractions survive a change of screen *size* and not a change of *game*, and a
    box that lands on unrelated pixels reports a confident wrong number rather
    than failing -- the same rule `payline.geometry_for` and
    `gameclick.targets_for` follow.
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
