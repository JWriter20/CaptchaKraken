"""The off-lattice FP gate counts CLUSTERS of stray lines, not stray lines.

The bug: a reCAPTCHA 4x4 whose correct lattice already WON scoring was thrown away
by the post-loop off-lattice gate. One busy photo tile (a building with a roofline,
eaves and a railing) contributed three near-parallel same-colour edges packed into
a 34px band inside a single 97px cell. The gate forgave one and counted the other
two, exceeding MAX_OFF_LATTICE=1, so a perfectly detected grid returned nothing.

Three edges 17px apart cannot be three cell boundaries — cells have a minimum size.
They are one object, so they are one piece of evidence. Strays spread across the
grid (the textured-photo false-positive mode) still count separately and are still
rejected, which is what the window keeps intact.
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captchakraken.tool_calls.find_grid import (  # noqa: E402
    MAX_OFF_LATTICE,
    MIN_CELL,
    OFF_LATTICE_CLUSTER_PX,
)


def count_clusters(positions, window=OFF_LATTICE_CLUSTER_PX):
    """The gate's own collapsing rule, stated once so the tests below assert on
    behaviour rather than on the nested closure's internals."""
    p = sorted(positions)
    return sum(1 for i, v in enumerate(p) if i == 0 or v - p[i - 1] > window)


def test_one_busy_tile_is_one_piece_of_evidence():
    """The exact failure: recaptcha_1779620454115_73tcr, cell pitch 97, strays at
    360/377/394 between lattice anchors 320 and 416."""
    assert count_clusters([359.9, 377.5, 394.0]) == 1


def test_a_busy_tile_no_longer_trips_the_gate():
    """One forgiven clean stray plus the collapsed cluster stays within budget."""
    assert count_clusters([359.9, 377.5, 394.0]) <= MAX_OFF_LATTICE


def test_strays_spread_across_the_grid_still_count_separately():
    """The textured-photo FP mode must keep failing: edges scattered a whole cell
    apart are independent evidence, not one object."""
    assert count_clusters([120.0, 220.0, 330.0, 450.0]) == 4


def test_the_window_is_below_the_minimum_cell_size():
    """Two real cell boundaries are at least MIN_CELL apart, so the window must sit
    well under that or the gate would collapse genuine separators."""
    assert OFF_LATTICE_CLUSTER_PX < MIN_CELL


def test_the_window_sits_inside_its_measured_plateau():
    """Sweeping the real corpus, 17..23 all scored 1091/1106 targets at 2/458 false
    positives; 16 lost both busy-tile 4x4s and 24 added a textured-drag-puzzle FP.
    Pinning the range stops a later 'tidy-up' from moving it onto a cliff edge."""
    assert 17 <= OFF_LATTICE_CLUSTER_PX <= 23
