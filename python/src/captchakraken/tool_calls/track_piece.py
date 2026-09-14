from __future__ import annotations

from typing import Optional, Sequence

import cv2
import numpy as np

_DIFF_THRESHOLD = 18

_MIN_CHANGED_PIXELS = 40


_SAME_WIDTH_TOLERANCE = 0.25

_CLEARED_MARK_FRACTION = 0.5

_MARK_GAP_PX = 10

_MIN_COLUMN_PIXELS = 2


def _change_mask(
    before_path: str,
    after_path: str,
    exclude: Optional[Sequence[float]] = None,
):
    a = cv2.imread(before_path, cv2.IMREAD_GRAYSCALE)
    b = cv2.imread(after_path, cv2.IMREAD_GRAYSCALE)
    if a is None or b is None or a.shape != b.shape:
        return None

    mask = (cv2.absdiff(a, b) > _DIFF_THRESHOLD).astype(np.uint8)

    if exclude is not None:
        x1, y1, x2, y2 = (int(round(float(v))) for v in exclude)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(mask.shape[1], x2), min(mask.shape[0], y2)
        if x2 > x1 and y2 > y1:
            mask[y1:y2, x1:x2] = 0

    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    if int(mask.sum()) < _MIN_CHANGED_PIXELS:
        return None
    return mask


def _marks(mask) -> list:
    on = (mask.sum(axis=0) >= _MIN_COLUMN_PIXELS)
    marks: list = []
    for x in np.nonzero(on)[0]:
        x = int(x)
        if marks and x - marks[-1][1] <= _MARK_GAP_PX:
            marks[-1][1] = x + 1
        else:
            marks.append([x, x + 1])
    return marks


def changed_bbox(
    before_path: str,
    after_path: str,
    exclude: Optional[Sequence[float]] = None,
) -> Optional[list]:
    mask = _change_mask(before_path, after_path, exclude)
    if mask is None:
        return None
    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def locate_piece(
    before_path: str,
    after_path: str,
    travel: float,
    exclude: Optional[Sequence[float]] = None,
) -> Optional[dict]:
    mask = _change_mask(before_path, after_path, exclude)
    if mask is None:
        return None
    marks = _marks(mask)
    if not marks:
        return None

    ghost_w = float(marks[0][1] - marks[0][0])
    for x1, x2 in reversed(marks[1:]):
        piece_w = float(x2 - x1)
        if (piece_w <= _CLEARED_MARK_FRACTION * travel
                and abs(piece_w - ghost_w) <= max(3.0, _SAME_WIDTH_TOLERANCE * ghost_w)):
            return {"centre": (x1 + x2) / 2.0, "width": piece_w}

    left, right = float(marks[0][0]), float(marks[-1][1])
    width = right - left
    if travel > 0 and width < travel:
        centre = left + width / 2.0
        return {"centre": centre + travel if centre - travel < 0 else centre,
                "width": width}
    piece_w = width - max(0.0, travel)
    if piece_w <= 0:
        return None
    return {"centre": right - piece_w / 2.0, "width": piece_w}
