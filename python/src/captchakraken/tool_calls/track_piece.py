"""Track the puzzle piece of a slide captcha by what MOVED, not by what it is.

A puzzle-piece slider gives the model one thing to say — where the gap is — and
gives the driver no way to act on it directly. You drag a handle somewhere else
on the widget; the piece follows at a ratio nobody publishes, clamped to a track
whose ends you cannot read off the picture. Vendors vary the ratio precisely so
that a solver cannot compute the handle offset from the gap position.

So the driver does not compute it. It sweeps the handle at the gap, looks at
what changed on screen, and corrects. This module is the "looks at what changed"
step, and nothing more:

    changed_bbox(before, after, exclude=...) -> [x1, y1, x2, y2] | None
    locate_piece(before, after, travel, exclude=...) -> {centre, width} | None

The box is the union of every pixel that differs between two screenshots of the
same widget. During a slider drag exactly one thing has moved — the piece — so
that union is:

    left edge  = the piece's ORIGINAL left edge   (the vacated ground)
    right edge = the piece's CURRENT right edge

Neither edge alone gives the piece's centre, but the width is
`piece_width + travel`, so the caller can recover both from two boxes taken at
two known handle offsets. `locate_piece` does better than that where it can: a
sweep long enough to carry the piece clear of the ground it vacated leaves TWO
separate marks in the frame, and the right-hand one IS the piece — measured, not
inferred, from a single look. See `PageSolver._execute_slide`.

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


# Two marks in the frame are the piece and the ground it vacated only if they
# are the SAME OBJECT — so they must be the same width. This is the tolerance on
# that check, and it is what keeps a board that animates somewhere else in frame
# from being read as the piece: a coincidence would have to match the piece's
# width to within a quarter of it.
_SAME_WIDTH_TOLERANCE = 0.25

# TWO marks arise two ways, and this tells them apart.
#
#   the piece CLEARED the ground it vacated  ->  each mark is the PIECE wide,
#                                                and they are `travel` apart
#   the piece only PARTLY left it            ->  each mark is the TRAVEL wide,
#                                                and they are a piece apart
#
# Both are two equal-width marks, so the widths alone cannot say which. What
# separates them is scale: a sweep at the gap moves the piece several times its
# own width, so a mark no more than half the travel is the piece, and a mark of
# about the travel is the sliver at either end of an overlap. `travel` is only
# BELIEVED — it carries whatever error is in the assumed ratio — so the two
# regimes have to be far apart for the test to survive that, and at the boundary
# (a piece that just barely cleared) the two readings agree anyway.
_CLEARED_MARK_FRACTION = 0.5

# Columns of stillness that separate one mark from the next. Generous, because
# the two marks this has to tell apart are a whole sweep away from each other,
# while a piece with a notch cut out of it can go still for several columns in
# the middle.
_MARK_GAP_PX = 10

# A column counts as changed at this many changed pixels. One is speckle the
# open above did not catch.
_MIN_COLUMN_PIXELS = 2


def _change_mask(
    before_path: str,
    after_path: str,
    exclude: Optional[Sequence[float]] = None,
):
    """The cleaned-up mask of what moved, or None when nothing did."""
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
    return mask


def _marks(mask) -> list:
    """The mask's horizontal extents, left to right, as [x1, x2) pairs.

    Projected onto the x axis rather than found as 2-D components: what is being
    told apart here is two objects side by side on one rail, and a piece that
    breaks into two blobs vertically (a notch, a highlight) is still one mark.
    """
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
    """Bounding box of everything that moved between two shots of one widget.

    `exclude` is an [x1, y1, x2, y2] rectangle in the same pixel space, masked
    out before measuring — pass the slider handle's own box.

    Returns [x1, y1, x2, y2] in pixels, or None when nothing moved (or the
    images are unreadable / mismatched, which is the same answer to the caller:
    steer by the last good reading instead of by noise).
    """
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
    """Where the piece is NOW and how wide it is, in this frame's pixels.

    `travel` is how far the piece is believed to have moved since `before_path`
    — the handle's offset times the ratio, in the SAME pixel space as the shots.
    It is what tells the three readings of one mask apart:

    TWO MARKS, EACH WELL UNDER THE TRAVEL AND THE SAME WIDTH AS EACH OTHER. The
    sweep carried the piece clear of the ground it vacated, so the left mark is
    that ground and the right one IS the piece. Measured, not inferred: no
    ratio, no algebra, no inherited bias. This is the case a sweep at the gap
    produces and two 24px calibration nudges never could.

    A SPAN NARROWER THAN THE TRAVEL. Only one of the two left a mark. A piece
    drawn as a thin outline over a photograph vacates ground that barely changes
    and so shows only where it landed; a piece swept off the end of the board
    shows only where it was. They are told apart by where the piece would have
    had to START: a mark that is the piece began a `travel` to its left, and
    when that is off the edge of the board it cannot be — so that mark is the
    vacated ground and the piece is a travel beyond it, which is what lets the
    caller come back for it.

    ANYTHING ELSE. The span holds both the piece and its ghost, overlapping or
    not, and is `piece_width + travel` wide.

    None when nothing moved, or when what moved cannot be read as a piece at
    all.
    """
    mask = _change_mask(before_path, after_path, exclude)
    if mask is None:
        return None
    marks = _marks(mask)
    if not marks:
        return None

    # The leftmost mark is the ground the piece vacated — the piece starts at
    # the left of these widgets and nothing is further left than where it was.
    # The piece is then the RIGHTMOST mark that is still the same object: same
    # width, and small against the distance travelled. Searched rather than
    # taken as `marks[-1]`, because a board that animates a badge somewhere else
    # puts a mark in the frame that is neither.
    ghost_w = float(marks[0][1] - marks[0][0])
    for x1, x2 in reversed(marks[1:]):
        piece_w = float(x2 - x1)
        if (piece_w <= _CLEARED_MARK_FRACTION * travel
                and abs(piece_w - ghost_w) <= max(3.0, _SAME_WIDTH_TOLERANCE * ghost_w)):
            return {"centre": (x1 + x2) / 2.0, "width": piece_w}

    # Everything else is read off the SPAN: its left edge is where the piece
    # was, its right edge is where the piece is, and its width is the piece
    # plus the distance it went.
    left, right = float(marks[0][0]), float(marks[-1][1])
    width = right - left
    if travel > 0 and width < travel:
        # Too narrow to hold both the piece and the distance it went, so it is
        # ONE of the two — and only one mark, because the other left no trace.
        # WHICH one is decided by where the piece would have had to start: if
        # this mark is the piece, it began a `travel` to the left of here, and
        # when that is off the edge of the board it cannot be. Then, and only
        # then, this is the ground the piece vacated on its way off the far
        # side, and the piece is a travel beyond it.
        centre = left + width / 2.0
        return {"centre": centre + travel if centre - travel < 0 else centre,
                "width": width}
    piece_w = width - max(0.0, travel)
    if piece_w <= 0:
        return None
    return {"centre": right - piece_w / 2.0, "width": piece_w}
