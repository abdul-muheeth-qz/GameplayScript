"""
Dynamic UI-panel detection (content-based, NOT fixed coordinates).

Slot-game meter values are almost always shown inside a flat-colored
(usually near-black) rounded panel/badge with bright text on top — visually
distinct from the busy, colorful artwork behind it. We detect these panels
by their COLOR PROPERTIES (dark AND low-saturation, i.e. "true black/grey",
which excludes warm-colored dark shadows in the artwork), never by assuming
a screen position. This works across different screenshots/resolutions/
layouts because it reacts to what's actually drawn on screen.
"""
import statistics

import cv2
import numpy as np


def detect_dark_panels(image, min_area_frac=0.0008):
    h, w = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1].astype(np.int32)
    v = hsv[:, :, 2].astype(np.int32)

    mask = ((v < 70) & (s < 70)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if cw * ch < min_area_frac * w * h:
            continue
        # skip absurd aspect ratios (thin lines/borders, not panels)
        if cw < 15 or ch < 10:
            continue
        boxes.append((x, y, cw, ch))
    return boxes


def group_panels_into_rows(boxes, y_tol_frac=0.5, height_ratio_max=2.2, width_ratio_max=2.3):
    """Group panels that likely belong to the same meter bar: similar
    vertical center, similar height, AND similar width. Meter cells in a
    bar (CASH/WIN/BET, or jackpot badges) tend to be comparably wide bars;
    unrelated small UI elements (buttons, denomination badges) are much
    narrower and would otherwise sneak into the group and skew the row's
    shared baseline statistics."""
    if not boxes:
        return []
    boxes_sorted = sorted(boxes, key=lambda b: b[1])
    rows = []
    for b in boxes_sorted:
        x, y, cw, ch = b
        cy = y + ch / 2.0
        placed = False
        for row in rows:
            row_heights = [rb[3] for rb in row]
            row_widths = [rb[2] for rb in row]
            row_h_med = statistics.median(row_heights)
            row_w_med = statistics.median(row_widths)
            row_cys = [rb[1] + rb[3] / 2.0 for rb in row]
            row_cy_med = statistics.median(row_cys)

            h_ratio = max(ch, row_h_med) / max(1, min(ch, row_h_med))
            w_ratio = max(cw, row_w_med) / max(1, min(cw, row_w_med))
            tol = y_tol_frac * max(ch, row_h_med)
            if (h_ratio <= height_ratio_max and w_ratio <= width_ratio_max
                    and abs(cy - row_cy_med) <= tol):
                row.append(b)
                placed = True
                break
        if not placed:
            rows.append([b])
    return rows
