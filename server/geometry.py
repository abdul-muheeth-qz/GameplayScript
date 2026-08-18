"""Normalized boxes, and the one rule for turning them into pixels.

A box is `[x0, y0, x1, y1]` as fractions of an image's width and height. Both `extract` and
`payline` crop by one, so the rounding rule lives here -- in a module with no dependencies.
Fractions survive a change of screen size but not of aspect ratio or game, so each caller keys
its boxes by game.
"""

from __future__ import annotations


def pixel_box(box, width: int, height: int) -> tuple[int, int, int, int] | None:
    """A normalized box as integer pixel edges on a width x height image.

    Clamped to the image, None when the box is empty or inverted. Each edge is rounded
    independently, so two boxes sharing an edge land on the same pixel at any image size.
    Cropping itself is `server.utils.crop_roi`.
    """
    x0f, y0f, x1f, y1f = box
    x0 = max(0, min(width, int(round(x0f * width))))
    y0 = max(0, min(height, int(round(y0f * height))))
    x1 = max(0, min(width, int(round(x1f * width))))
    y1 = max(0, min(height, int(round(y1f * height))))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1
