"""Crop an image to whichever of a list of normalized boxes reads best.

    crop = crop_best_box(image, boxes, score=read_it)

`boxes` is a list of `{"label": str, "box": [x0, y0, x1, y1]}` -- normalized
fractions of the image, one entry per layout the caller knows about. `score`
is how the caller decides a box landed on the right thing: it is handed each
crop and answers `(rank, reading)`, where `rank` is an int (higher is better)
and `reading` is whatever the caller wants carried back out. Splitting it that
way is what keeps this module free of OCR, of Tesseract and of any one stage's
idea of what "reads well" means -- `extract` scores a crop by how many meter
fields it resolved, and something else could score it any other way.

**The best box wins, not the first that scored anything**, and that rule is the
reason this function exists rather than a loop at each call site. Boxes
describe different layouts of the same thing, and a box aimed at another layout
can land somewhere unrelated on this image and still scrape one plausible
reading out of it. Under a first-past-the-post rule, adding a box for a new
layout silently degrades every image listed ahead of it -- measured in
`extract`, where adding this cabinet's box broke four of the fourteen sample
images that had been fine.

Three details are load-bearing and each is easy to "simplify" wrongly:

- **`best_rank` starts below zero**, so the first croppable box always becomes
  the winner. A box that scored nothing is still the crop this chose, and
  returning None instead would leave the caller with no image at all. Callers
  that care can see `rank == 0` and say so.
- **Ties go to the earlier box**, so the caller's list order reads as a
  preference order.
- **`confident` stops the search early.** Scoring is not assumed cheap -- in
  `extract` it is the whole downstream extraction, so trying the rest of the
  list after a box has clearly won costs seconds per image. Leave it None to
  score every box.
"""

from __future__ import annotations

from typing import Any, Callable, NamedTuple, Sequence

from ..geometry import crop_normalized_box


class RoiCropError(ValueError):
    """No box in the list could be cropped from the image."""


class BoxCrop(NamedTuple):
    """The winning crop, which box it came from, and what the scorer saw.

    `label` is the winning entry's own label, so a caller can put it in a
    record and answer "which pixels was this read from?" afterwards. `reading`
    is whatever `score` returned alongside the rank -- scoring a crop is often
    the same work the caller is about to do anyway, so it is handed back rather
    than thrown away and paid for twice.
    """

    image: Any
    label: str
    rank: int
    reading: Any


def crop_best_box(image, boxes: Sequence[dict],
                  score: Callable[[Any], tuple[int, Any]],
                  confident: int | None = None,
                  boxes_name: str = "the box list") -> BoxCrop:
    """The best-reading crop of `image` among `boxes`. See the module docstring.

    `boxes_name` names the list in the error raised when nothing can be
    cropped, so the message points at the constant a person has to edit rather
    than at this function.

    Raises `RoiCropError` when no box yields a crop -- the list is empty, or
    every box in it is inverted or off the image.
    """
    best: BoxCrop | None = None
    # -1, not 0: the first croppable box must always become `best`, even when it
    # scores nothing. See the module docstring.
    best_rank = -1

    for candidate in boxes:
        crop = crop_normalized_box(image, candidate["box"])
        if crop is None or crop.size == 0:
            continue
        rank, reading = score(crop)
        # Strictly greater, so ties go to the earlier box and the caller's list
        # order stays a preference order.
        if rank > best_rank:
            best = BoxCrop(crop, candidate.get("label", "unnamed"), rank, reading)
            best_rank = rank
        if confident is not None and best_rank >= confident:
            break

    if best is None:
        raise RoiCropError(
            f"no box in {boxes_name} could be cropped from this frame -- the list is "
            f"empty, or every box in it is inverted or off the image")
    return best
