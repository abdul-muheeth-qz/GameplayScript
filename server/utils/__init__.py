"""Stage-agnostic helpers. Arguments only, no config of their own, so any stage can import them."""

from .roi_crop import RoiCropError, crop_roi, image_size

__all__ = ["RoiCropError", "crop_roi", "image_size"]
