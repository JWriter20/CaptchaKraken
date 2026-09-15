import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captchakraken.solver import (
    _GRID_REGULARITY_TOL,
    _lattice_irregularity,
)


def lattice(k, cell=100, gap=0):
    step = cell + gap
    return [(c * step, r * step, c * step + cell, r * step + cell)
            for r in range(k) for c in range(k)]


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
    assert _lattice_irregularity(lattice(k, gap=8)) == 0.0


def test_the_false_lattice_is_rejected():
    assert _lattice_irregularity(THE_FALSE_LATTICE) > _GRID_REGULARITY_TOL


def test_a_sheared_lattice_is_rejected():
    boxes = [(x1 + 6 * (y1 // 100), y1, x2 + 6 * (y1 // 100), y2)
             for (x1, y1, x2, y2) in lattice(3)]
    assert _lattice_irregularity(boxes) > _GRID_REGULARITY_TOL


def test_a_lattice_clipped_at_the_frame_edge_is_rejected():
    boxes = [(x1, y1, min(x2, 250), y2) for (x1, y1, x2, y2) in lattice(3)]
    assert _lattice_irregularity(boxes) > _GRID_REGULARITY_TOL


def test_a_pixel_of_antialiasing_is_not_a_false_positive():
    boxes = list(lattice(3))
    boxes[4] = (boxes[4][0] + 1, boxes[4][1] + 1, boxes[4][2], boxes[4][3])
    assert _lattice_irregularity(boxes) <= _GRID_REGULARITY_TOL


@pytest.mark.parametrize("boxes", [[], lattice(3)[:8], lattice(3)[:1]])
def test_a_count_that_is_not_a_square_has_no_answer(boxes):
    assert _lattice_irregularity(boxes) is None


def test_a_degenerate_cell_has_no_answer():
    boxes = list(lattice(3))
    boxes[0] = (0, 0, 0, 0)
    boxes[4] = (0, 0, 0, 0)
    boxes[8] = (0, 0, 0, 0)
    boxes[1] = (0, 0, 0, 0)
    boxes[2] = (0, 0, 0, 0)
    assert _lattice_irregularity(boxes) is None
