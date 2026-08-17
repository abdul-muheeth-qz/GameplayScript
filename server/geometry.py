"""Normalized boxes, and the one rule for turning them into pixels.

A normalized box is `[x0, y0, x1, y1]` as fractions (0.0-1.0) of an image's width and
height. Two stages crop by one now -- `extract` to the meter strip and `payline` to the
reels window -- so the rounding-and-clamping rule lives here rather than being written
twice. It is the same reason `frames.py` exists: a contract shared by more than one
package belongs in a module with no dependencies, so nobody drags OpenCV or Pillow into
a stage that does not want it.

Fractions rather than pixels because the game window moves. Measured on this cabinet:
612x961 for HuffNPuffLink, 1080x1849 for FortuneOx, and the same game observed at
638x1048 and 510x928 an hour apart. A box expressed as fractions survives all of that;
one expressed in pixels is a different box on every one of them.

What fractions do *not* survive is a change of **aspect ratio** or a change of game art.
Scaling the same layout up or down is safe -- the payline geometry was checked from 0.6x
to 3.0x and read the identical grid at every size -- but a different game needs its own
numbers, which is why both callers key their boxes by layout rather than sharing one.
"""

from __future__ import annotations


def pixel_box(box, width: int, height: int) -> tuple[int, int, int, int] | None:
    """A normalized `[x0, y0, x1, y1]` as integer pixel edges on a width x height image.

    Clamped to the image, and None when the box is empty or inverted -- callers have to
    handle that rather than being handed a zero-width crop. **The clamping is why every
    caller validates its own numbers first**: a box that runs off the image is silently
    trimmed to fit and reads as a bad crop rather than as the typo it is.

    The edges come from the fractions independently, which is what lets two boxes sharing
    an edge round to exactly the same pixel -- so a strip cut into adjacent boxes has no
    gap or overlap at the seams, whatever the image size.

    This is the definition and nothing more. **Actually cropping an image is
    `server.utils.crop_roi`**, which both stages call: it handles a numpy array and a PIL
    image alike and raises with the box named, and it lives in `utils/` so this module
    stays free of any opinion about what an image is. A numpy-only `crop_normalized_box`
    used to sit here for the box race's benefit; the race is gone and so is it.
    """
    x0f, y0f, x1f, y1f = box
    x0 = max(0, min(width, int(round(x0f * width))))
    y0 = max(0, min(height, int(round(y0f * height))))
    x1 = max(0, min(width, int(round(x1f * width))))
    y1 = max(0, min(height, int(round(y1f * height))))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1
