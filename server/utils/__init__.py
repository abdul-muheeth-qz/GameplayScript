"""Small, stage-agnostic helpers.

Everything here takes what it needs as arguments and reads no configuration of
its own, so any stage can import it without dragging that stage's settings
along. It is the same rule `server/frames.py` and `server/geometry.py` follow,
and the reason those two live at the top of `server/` rather than inside the
package that happened to need them first.

The line between this and `server/geometry.py`: geometry.py is the *definition*
of a normalized box and how it becomes pixels, depended on by two stages and
frozen. This is the reusable work built on top of that definition -- currently
one function, `crop_roi`, which is how both `extract` and `payline` turn a box
into pixels.
"""

from .roi_crop import RoiCropError, crop_roi, image_size

__all__ = ["RoiCropError", "crop_roi", "image_size"]
