"""Crop an image to one normalized box, on a numpy array (`extract`) or a PIL image (`payline`).

`what` names the thing being cropped in the error, so a bad box points at the config key to edit.
One box in, that region out: no scoring, no candidate list, no choosing between boxes.
"""

from __future__ import annotations

from ..geometry import pixel_box


class RoiCropError(ValueError):
    """The box does not describe a region of this image."""


def image_size(image) -> tuple[int, int]:
    """`(width, height)` of a PIL image or a numpy array."""
    if hasattr(image, "width") and hasattr(image, "height"):
        return image.width, image.height
    height, width = image.shape[:2]
    return width, height


def crop_roi(image, box, what: str = "the ROI"):
    """`image` cropped to the normalized `box`. Same type in, same type out.

    Raises rather than returning None: there is nothing behind this crop, so a caller with no
    region has nothing to read.
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
