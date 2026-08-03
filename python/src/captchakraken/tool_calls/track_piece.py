"""Track the puzzle piece of a slide captcha by what MOVED, not by what it is.

A puzzle-piece slider gives the model one thing to say — where the gap is — and
gives the driver no way to act on it directly. You drag a handle somewhere else
on the widget; the piece follows at a ratio nobody publishes, clamped to a track
whose ends you cannot read off the picture. Vendors vary the ratio precisely so
that a solver cannot compute the handle offset from the gap position.

So the driver does not compute it. It presses the handle, nudges, looks at what
changed on screen, and closes the loop. This module is the "looks at what
changed" step, and nothing more: one function, one bounding box.

    changed_bbox(before, after, exclude=...) -> [x1, y1, x2, y2] | None

The box is the union of every pixel that differs between two screenshots of the
same widget. During a slider drag exactly one thing has moved — the piece — so
that union is:

    left edge  = the piece's ORIGINAL left edge   (the vacated ground)
    right edge = the piece's CURRENT right edge

which is what makes it useful: neither edge alone gives the piece's centre, but
two of these boxes at two known handle offsets give both the piece's width and
the handle-to-piece ratio, by simple algebra the caller does. See
`PageSolver._execute_slide`.

`exclude` is the reason this takes an argument at all. The handle is also
moving, and on most vendors so is a filled progress bar behind it, and both are
inside the same screenshot. Left in, they would dominate the union and the
tracked "piece" would just be the handle. The caller passes the handle's own
rectangle — it has the element, so it knows exactly where it is — and this masks
it out. Everything else in the frame is static during a drag.

Pure OpenCV, no network, byte-deterministic.
"""

from __future__ import annotations

from typing import Optional, Sequence

import cv2
import numpy as np

# A pixel counts as changed at this 0–255 grayscale delta. Low enough to catch a
# piece sliding over background of similar luminance, high enough to ignore JPEG
# ringing and the 1–2 level jitter of an antialiased edge redrawn a subpixel
# over.
_DIFF_THRESHOLD = 18

# Below this many changed pixels the frame is treated as UNCHANGED. A press that
# has not moved yet, or a drag against the end of the track, produces a handful
# of stray pixels; calling that a piece would hand the caller a garbage box and
# it would steer by it.
_MIN_CHANGED_PIXELS = 40


def changed_bbox(
    before_path: str,
    after_path: str,
    exclude: Optional[Sequence[float]] = None,
) -> Optional[list]:
    """Bounding box of everything that moved between two shots of one widget.

    `exclude` is an [x1, y1, x2, y2] rectangle in the same pixel space, masked
    out before measuring — pass the slider handle's own box.

    Returns [x1, y1, x2, y2] in pixels, or None when nothing moved (or the
    images are unreadable / mismatched, which is the same answer to the caller:
    steer by the last good reading instead of by noise).
    """
    a = cv2.imread(before_path, cv2.IMREAD_GRAYSCALE)
    b = cv2.imread(after_path, cv2.IMREAD_GRAYSCALE)
    if a is None or b is None or a.shape != b.shape:
        return None

    mask = (cv2.absdiff(a, b) > _DIFF_THRESHOLD).astype(np.uint8)

    if exclude is not None:
        x1, y1, x2, y2 = (int(round(float(v))) for v in exclude)
        # Clamp to the frame — a handle drawn flush with the widget edge, or one
        # whose box is reported a pixel outside it, must still mask.
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(mask.shape[1], x2), min(mask.shape[0], y2)
        if x2 > x1 and y2 > y1:
            mask[y1:y2, x1:x2] = 0

    # Erode away single-pixel speckle (compression noise, cursor artifacts)
    # before measuring: the box is an EXTREME of the mask, so one stray pixel in
    # the corner moves it as much as the piece does.
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    if int(mask.sum()) < _MIN_CHANGED_PIXELS:
        return None

    ys, xs = np.nonzero(mask)
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
