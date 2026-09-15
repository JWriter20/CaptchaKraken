"""Every test here is a bug that happened; box lists rather than images because the failures were in the arithmetic."""

import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captchakraken.tool_calls.find_grid import (
    MIN_CELL,
    MIN_IMAGE_AREA_COVERAGE,
    CELL_REGULARITY_TOL,
    _boxes_are_regular,
)
import captchakraken.tool_calls.find_grid as fg


def grid_boxes(x0, y0, cell, gap, rows, cols, jitter=None):
    out = []
    for r in range(rows):
        dx = (jitter[r] if jitter else 0)
        for c in range(cols):
            x = x0 + c * (cell + gap) + dx
            y = y0 + r * (cell + gap)
            out.append((x, y, x + cell, y + cell))
    return out


def test_a_plain_grid_is_regular():
    assert _boxes_are_regular(grid_boxes(10, 10, 100, 0, 3, 3), 3, 3)


@pytest.mark.parametrize("gap", [4, 10, 15, 25])
def test_a_grid_with_gutters_is_regular(gap):
    assert _boxes_are_regular(grid_boxes(10, 10, 100, gap, 3, 3), 3, 3), (
        f"a grid with a {gap}px gutter was called irregular")


def test_inter_row_rounding_is_not_a_phantom_separator():
    """Edges taken as the SET of every box's edge read [110, 1, 110, 1] on any slant and rejected 101 real hCaptcha grids."""
    boxes = grid_boxes(91, 138, 110, 0, 3, 3, jitter=[0, 1, 1])
    assert _boxes_are_regular(boxes, 3, 3), "inter-row rounding read as an uneven grid"


def test_duplicate_lines_one_pixel_apart_are_rejected():
    """A text captcha yielded column edges [206, 254, 255, 303] whose cells each measured a plausible ~50px."""
    boxes = []
    for y in (107, 157, 210):
        for x in (206, 254, 255):
            boxes.append((x, y, x + 48, y + 50))
    assert not _boxes_are_regular(boxes, 3, 3)


def test_uneven_columns_are_rejected():
    boxes = []
    for y in (0, 100, 200):
        for x in (0, 100, 260):
            boxes.append((x, y, x + 90, y + 90))
    assert not _boxes_are_regular(boxes, 3, 3)


def test_cells_below_the_minimum_are_rejected():
    tiny = MIN_CELL - 4
    assert not _boxes_are_regular(grid_boxes(0, 0, tiny, 0, 3, 3), 3, 3)


def test_regularity_tolerance_is_actually_applied():
    cell = 100
    inside = int(cell * (1 + CELL_REGULARITY_TOL * 0.5))
    outside = int(cell * (1 + CELL_REGULARITY_TOL * 2.0))
    ok = [(0, 0, cell, cell), (cell, 0, cell + inside, cell),
          (cell + inside, 0, cell + inside + cell, cell)]
    assert _boxes_are_regular(ok, 1, 3)
    bad = [(0, 0, cell, cell), (cell, 0, cell + outside, cell),
           (cell + outside, 0, cell + outside + cell, cell)]
    assert not _boxes_are_regular(bad, 1, 3)


def test_the_coverage_floor_sits_below_every_real_grid():
    """Measured over 2239 real grids the smallest coverage is 0.348; the floor must stay under it with room."""
    assert MIN_IMAGE_AREA_COVERAGE < 0.348, "coverage floor is above a real grid"
    assert MIN_IMAGE_AREA_COVERAGE >= 0.20, "floor so low it rejects nothing"


def test_the_two_coverage_constants_are_not_the_same_knob():
    """MIN_IMAGE_AREA_COVERAGE was first added under the existing name MIN_GRID_COVERAGE; the area check ran at 0.72 and detection went to 0/2210."""
    assert hasattr(fg, "MIN_IMAGE_AREA_COVERAGE")
    if hasattr(fg, "MIN_GRID_COVERAGE"):
        assert fg.MIN_IMAGE_AREA_COVERAGE != fg.MIN_GRID_COVERAGE, (
            "the area floor and the per-axis pitch ratio have collapsed into one "
            "value again — they are different quantities")
