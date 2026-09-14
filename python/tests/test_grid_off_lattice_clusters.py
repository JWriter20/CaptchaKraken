import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captchakraken.tool_calls.find_grid import (
    MAX_OFF_LATTICE,
    MIN_CELL,
    OFF_LATTICE_CLUSTER_PX,
)


def count_clusters(positions, window=OFF_LATTICE_CLUSTER_PX):
    p = sorted(positions)
    return sum(1 for i, v in enumerate(p) if i == 0 or v - p[i - 1] > window)


def test_one_busy_tile_is_one_piece_of_evidence():
    assert count_clusters([359.9, 377.5, 394.0]) == 1


def test_a_busy_tile_no_longer_trips_the_gate():
    assert count_clusters([359.9, 377.5, 394.0]) <= MAX_OFF_LATTICE


def test_strays_spread_across_the_grid_still_count_separately():
    assert count_clusters([120.0, 220.0, 330.0, 450.0]) == 4


def test_the_window_is_below_the_minimum_cell_size():
    assert OFF_LATTICE_CLUSTER_PX < MIN_CELL


def test_the_window_sits_inside_its_measured_plateau():
    assert 17 <= OFF_LATTICE_CLUSTER_PX <= 23
