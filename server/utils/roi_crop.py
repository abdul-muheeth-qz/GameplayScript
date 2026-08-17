"""Crop an image to one normalized box. The whole of it.

    crop = crop_roi(image, box, what="the meter ROI for HuffNPuffLink.exe")

`box` is a normalized `[x0, y0, x1, y1]` -- fractions of the image's width and
height, the form `server/geometry.py` defines. `what` names the thing being
cropped in the error, so a bad box points at the key a person has to edit
rather than at this function.

**It works on both a numpy array (`extract`, which reads frames with cv2) and a
PIL image (`payline`, which is Pillow end to end)**, which is the reason it is
here rather than written once per stage. It is the same crop either way; only
the way an image reports its size and hands back a sub-image differs, and that
is four lines rather than two copies of the rounding rule.

**There is no scoring, no candidate list and no choosing.** An earlier version
took a *list* of boxes and a scorer, cropped every one, and kept whichever
"read best" -- `crop_best_box`. It is gone, along with `BoxCrop` and the
`confident` early exit, because a stage that picks its own ROI cannot say which
pixels a number came from until after the fact, and a box aimed at another
layout can land somewhere unrelated and still scrape a plausible reading out of
it. Both callers now name the one box they mean, keyed by the active game, and a
crop that misses shows up as a value that could not be read -- which is the
failure worth having. Don't reintroduce a race here; if a new layout needs a
different box, it needs a `meter_roi` of its own in `game_config.json`.
"""

from __future__ import annotations

from ..geometry import pixel_box


class RoiCropError(ValueError):
    """The box does not describe a region of this image."""


def image_size(image) -> tuple[int, int]:
    """`(width, height)` of a PIL image or a numpy array.

    PIL is asked first, because a PIL image has no `.shape` while a numpy array
    has neither `.width` nor `.crop` -- so the two cases cannot be confused.
    """
    if hasattr(image, "width") and hasattr(image, "height"):
        return image.width, image.height
    height, width = image.shape[:2]
    return width, height


def crop_roi(image, box, what: str = "the ROI"):
    """`image` cropped to the normalized `box`. Same type in, same type out.

    Raises `RoiCropError` when the box is inverted, or lands off the image
    entirely, or is too small to contain a pixel at this image's size -- all of
    which `geometry.pixel_box` answers with None. It is raised rather than
    returned as None because there is nothing behind this: a caller with no crop
    has nothing to read, and the message names the box and the size it was
    empty on, which is what makes a typo in a fraction diagnosable.
    """
    width, height = image_size(image)
    edges = pixel_box(box, width, height)
    if edges is None:
        raise RoiCropError(
            f"{what} is {list(box)}, which is not a region of a {width}x{height} "
            f"image -- the box is inverted, off the image, or too small to hold a "
            f"pixel at this size. These are normalized fractions (0.0-1.0) of the "
            f"width and height, so x0 < x1 and y0 < y1")

    if hasattr(image, "crop"):
        return image.crop(edges)
    x0, y0, x1, y1 = edges
    return image[y0:y1, x0:x1]
