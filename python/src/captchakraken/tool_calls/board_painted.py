from __future__ import annotations

from typing import Optional, Sequence

CENTRE_BOX = (0.15, 0.25, 0.85, 0.75)

LEVEL_DELTA = 12

TEXTURE_FLOOR = 0.015


def centre_texture(path: str, box: Optional[Sequence[float]] = None) -> Optional[float]:
    import cv2
    import numpy as np

    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    h, w = img.shape[:2]
    b = tuple(box) if box is not None else CENTRE_BOX
    x1, y1 = int(b[0] * w), int(b[1] * h)
    x2, y2 = int(b[2] * w), int(b[3] * h)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    region = img[y1:y2, x1:x2].astype("int16")
    ground = float(np.median(region))
    return float((abs(region - ground) > LEVEL_DELTA).mean())


def board_is_painted(path: str, floor: Optional[float] = None,
                     box: Optional[Sequence[float]] = None) -> dict:
    f = TEXTURE_FLOOR if floor is None else float(floor)
    t = centre_texture(path, box)
    if t is None:
        return {"painted": None, "texture": None, "floor": f}
    return {"painted": bool(t >= f), "texture": round(t, 5), "floor": f}
