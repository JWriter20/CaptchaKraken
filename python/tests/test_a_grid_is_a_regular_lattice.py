"""A grid is a LATTICE, and `_is_real_grid` never asked whether it was one.

Every check inside `_is_real_grid` asks what is INSIDE the cells — per-cell
colour spread, the shared-background test that tells a sprite board from tiles.
A click puzzle drawn over a photograph answers all of them correctly, because
its "cells" really are colourful and really do differ from one another. The
thing it cannot fake is the geometry.

`find_grid` traces separator lines "of any border colour and small tilt" and
recovers the slant on its own, so a TILTED lattice is one of its legitimate
outputs. No vendor ships a tilted grid — every one of them lays the tiles out
with CSS, on the pixel — so that tolerance is headroom no true board needs and
a false positive does. Measured on the Tier 3 boards: a photographic backdrop's
own edges came back as nine cells sheared a few pixels per row and clipped at
the frame edge, cleared every content check, and the board was answered as a
grid. A grid answer is a LIST OF CELLS, so the mis-route does not read as a
wrong answer — it reads as a driver clicking six things on a board whose answer
is one press.

The numbers here are measured, not chosen: over the real captures, six per
family, every true grid `find_grid` finds is regular to the pixel — a GeeTest
3x3 photo grid, an hCaptcha 3x3 property grid, a Prosopo 3x3, and the reCAPTCHA
3x3 and 4x4, 25 detections, all 0.000 — against 0.128 for the false one.

No JS twin: both ports run this decision through the shared Python CLI, so
`solver.py` is the only copy. CLAUDE.md 1c.
"""
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captchakraken.solver import (  # noqa: E402
    _GRID_REGULARITY_TOL,
    _lattice_irregularity,
)


def lattice(k, cell=100, gap=0):
    """A perfect k x k lattice, row-major, the way find_grid returns one."""
    step = cell + gap
    return [(c * step, r * step, c * step + cell, r * step + cell)
            for r in range(k) for c in range(k)]


#: The detection that started this: nine cells traced off a photographic
#: backdrop on an hCaptcha click board. Sheared — each row starts 2px further
#: left than the one above — and clipped at x=446, the right edge of the frame,
#: so the last column is 83px wide against 94 for the others.
THE_FALSE_LATTICE = [
    (178, 112, 272, 196), (271, 115, 365, 198), (363, 117, 446, 200),
    (176, 193, 271, 276), (269, 196, 363, 279), (361, 198, 446, 281),
    (174, 274, 269, 357), (267, 276, 361, 360), (359, 279, 446, 362),
]


@pytest.mark.parametrize("k", [3, 4])
def test_a_true_lattice_is_irregular_by_nothing_at_all(k):
    assert _lattice_irregularity(lattice(k)) == 0.0


@pytest.mark.parametrize("k", [3, 4])
def test_the_gutters_between_tiles_do_not_count_as_irregularity(k):
    """Every vendor draws a gap between tiles. It is part of the lattice, not a
    departure from it — the cells are still the same size and still aligned."""
    assert _lattice_irregularity(lattice(k, gap=8)) == 0.0


def test_the_false_lattice_is_rejected():
    assert _lattice_irregularity(THE_FALSE_LATTICE) > _GRID_REGULARITY_TOL


def test_a_sheared_lattice_is_rejected():
    """The tilt find_grid is allowed to recover, and no vendor ever ships."""
    boxes = [(x1 + 6 * (y1 // 100), y1, x2 + 6 * (y1 // 100), y2)
             for (x1, y1, x2, y2) in lattice(3)]
    assert _lattice_irregularity(boxes) > _GRID_REGULARITY_TOL


def test_a_lattice_clipped_at_the_frame_edge_is_rejected():
    """The other half of the false positive: a lattice traced past the edge of
    the picture has a short last column, and real tiles are all one size."""
    boxes = [(x1, y1, min(x2, 250), y2) for (x1, y1, x2, y2) in lattice(3)]
    assert _lattice_irregularity(boxes) > _GRID_REGULARITY_TOL


def test_a_pixel_of_antialiasing_is_not_a_false_positive():
    """Why the bar is not zero. A separator traced over an antialiased edge can
    land a pixel out, and that must not cost a real board its grid."""
    boxes = list(lattice(3))
    boxes[4] = (boxes[4][0] + 1, boxes[4][1] + 1, boxes[4][2], boxes[4][3])
    assert _lattice_irregularity(boxes) <= _GRID_REGULARITY_TOL


@pytest.mark.parametrize("boxes", [[], lattice(3)[:8], lattice(3)[:1]])
def test_a_count_that_is_not_a_square_has_no_answer(boxes):
    """None is "the question does not apply", and the caller must not read it as
    "regular" — `_grid_dims` has already refused anything but 9 or 16 by the
    time this runs, so None here means a caller passed something else."""
    assert _lattice_irregularity(boxes) is None


def test_a_degenerate_cell_has_no_answer():
    """A zero-width cell makes every ratio meaningless; say so rather than
    dividing by it."""
    boxes = list(lattice(3))
    boxes[0] = (0, 0, 0, 0)
    boxes[4] = (0, 0, 0, 0)
    boxes[8] = (0, 0, 0, 0)
    boxes[1] = (0, 0, 0, 0)
    boxes[2] = (0, 0, 0, 0)
    assert _lattice_irregularity(boxes) is None
