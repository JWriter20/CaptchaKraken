"""Has the widget PAINTED its puzzle yet, or are we photographing the reset?

Every vendor here tears the board down and rebuilds it — on the first open, on
a refresh, and on every round after a refused answer. For a few hundred
milliseconds in the middle of that the panel is its own chrome and nothing else:
the frame, the caption, the footer icons, and a white hole where the puzzle
goes.

Photographing that hole and sending it up the wire is not a near-miss, it is a
question with no content in it, and the model answers it the only way it can —
with the middle of the image. Measured on gt4.geetest.com's slide demo,
2026-09-12, over ten live attempts: six of fifty-two requests carried a board
with under 5% ink, every one of them came back `to:[480,310]`-ish (dead centre),
and the driver executed one of them, burning a round on a drag to nowhere.

WHY THE SETTLE GATE DOES NOT CATCH IT. `waitForElementSettled` polls for
STILLNESS, and a blank panel is perfectly still — it is the stillest the widget
ever is. The existing pairing that covers this is `waitForHcaptchaChallengeImages`,
which asks the DOM, and it only knows hCaptcha. This asks the picture instead,
so it holds for every vendor.

WHAT "PAINTED" MEANS HERE, and what it deliberately does not mean. Not "is the
board dark", not "is there enough ink" — geetest_v4_svg is line art on white and
would fail either. The question is whether the middle of the panel carries any
STRUCTURE at all: a blank board is one flat colour, and a puzzle, however pale,
is not. So the measure is the share of centre pixels that differ from the
centre's own modal value, and the floor is set just above nothing.

The caller must treat a `False` as "wait a moment longer", never as "give up" —
see `waitForBoardPainted` in solver.ts and `_wait_for_board_painted` in
page_solver.py, both of which proceed anyway when the budget runs out. A gate
that can refuse to ever take a picture is worse than the blank picture.

Pure OpenCV, no network, byte-deterministic.
"""

from __future__ import annotations

from typing import Optional, Sequence

#: The slice of the panel the puzzle lives in, as fractions of width/height.
#: Trimmed in from every edge on purpose: the caption above and the icon row
#: below are chrome that paints EARLY and paints on a blank board too, so a
#: whole-panel measure reads them as content and calls the hole loaded.
CENTRE_BOX = (0.15, 0.25, 0.85, 0.75)

#: How far from the centre's own modal grey a pixel must sit to count as
#: structure. Below this is JPEG noise and anti-aliasing on a flat fill.
LEVEL_DELTA = 12

#: The share of the centre that must carry structure. Set just above nothing
#: rather than at any particular puzzle's density: the separation being relied
#: on is blank-versus-anything (measured 0.000-0.004 blank against 0.19-0.56
#: painted on the GeeTest slide), not a threshold between puzzle kinds.
TEXTURE_FLOOR = 0.015


def centre_texture(path: str, box: Optional[Sequence[float]] = None) -> Optional[float]:
    """Share of the centre box that differs from the centre's modal value.

    None when the image cannot be read or is too small to have a middle — the
    caller cannot tell "blank" from "unreadable" otherwise, and those want
    different handling.
    """
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
    """`{painted, texture, floor}`. `painted` is None when the image is unreadable."""
    f = TEXTURE_FLOOR if floor is None else float(floor)
    t = centre_texture(path, box)
    if t is None:
        return {"painted": None, "texture": None, "floor": f}
    return {"painted": bool(t >= f), "texture": round(t, 5), "floor": f}
