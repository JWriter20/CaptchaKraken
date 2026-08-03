"""A chosen lattice must SEPARATE something: its gutters need different content on
either side.

The bug: a rotating-object video keyframe produced a confident 12-cell grid whose
"gutters" ran across a smooth teal background, separating nothing.

Two things this gate got wrong before it worked, both of which the tests below pin
because both cost real detections when they were wrong:

1. Probing a FIXED pixel distance either side. hcaptcha's gutters are ~13px median
   and can run far wider, so the probe landed INSIDE the gutter and compared gutter
   to gutter — real separators read as zero contrast. Distances scale with the cell
   pitch instead, so they always clear the gutter and reach cell content.
2. Scoring a separator by its WEAKER side (`min`). That punishes a real gutter with
   one pale neighbour, which describes most of hcaptcha: under `min` the lowest true
   hcaptcha grid scored 2.7 while the false positives scored 9.0 and 7.2 — the real
   grids were BELOW the false positives, so no cutoff existed. Averaging the two
   sides moves the lowest true grid to 17.4 against false positives at 18.0 and 14.1.

The gate also runs on the CHOSEN grid, never as a per-line filter: filtering lines
earlier removes the stray lines the off-lattice gate counts as evidence, which was
measured to ADD false positives (2 -> 4 -> 6).
"""
import os
import sys

import numpy as np
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captchakraken.tool_calls.find_grid import (  # noqa: E402
    FLANK_PITCH_FRACS,
    GRID_FLANK_MIN_DE,
    MAX_THICKNESS,
    _flank_contrast,
    _to_lab,
)


class FakeLine:
    """The attributes _flank_contrast reads off a traced separator."""

    def __init__(self, orientation, pos, lo, hi, color_lab):
        self.orientation = orientation
        self.midline_pos = pos
        self.angle = 0.0
        self.color_lab = np.asarray(color_lab, dtype=np.float64)
        self.start = (lo, pos) if orientation == "h" else (pos, lo)
        self.end = (hi, pos) if orientation == "h" else (pos, hi)


def image(w, h, fill):
    return np.full((h, w, 3), fill, dtype=np.uint8)


def vertical_gutter(gutter_px, cell_bgr=(30, 30, 30), pitch=120):
    """A white vertical gutter `gutter_px` wide at x=pitch, dark cells either side."""
    w, h = pitch * 2, 300
    img = image(w, h, 0)
    img[:, :] = cell_bgr
    half = gutter_px // 2
    img[:, pitch - half:pitch + half + 1] = 255
    lab = _to_lab(img)
    line = FakeLine("v", float(pitch), 0.0, float(h - 1), lab[h // 2, pitch])
    return lab, line, pitch


def test_a_real_separator_between_filled_cells_has_high_contrast():
    lab, line, pitch = vertical_gutter(4)
    assert _flank_contrast(lab, line, pitch) > GRID_FLANK_MIN_DE


def test_a_line_that_separates_nothing_has_no_contrast():
    """The false-positive mode: uniform surroundings, so the flanks match the line."""
    w, h, pitch = 240, 300, 120
    img = image(w, h, 200)
    lab = _to_lab(img)
    line = FakeLine("v", float(pitch), 0.0, float(h - 1), lab[h // 2, pitch])
    assert _flank_contrast(lab, line, pitch) < GRID_FLANK_MIN_DE


@pytest.mark.parametrize("gutter_px", [4, 13, 20, MAX_THICKNESS + 6])
def test_a_wide_gutter_is_still_measured_against_the_cells(gutter_px):
    """The hcaptcha regression. A fixed-offset probe sat inside gutters this wide and
    reported no contrast, rejecting genuine grids."""
    lab, line, pitch = vertical_gutter(gutter_px)
    assert _flank_contrast(lab, line, pitch) > GRID_FLANK_MIN_DE, (
        f"a {gutter_px}px gutter was measured against itself, not against its cells")


def test_the_probe_clears_any_gutter_the_tracer_would_accept():
    """Every probe distance must land beyond MAX_THICKNESS for a typical cell pitch,
    or the innermost ones sample the gutter."""
    assert min(FLANK_PITCH_FRACS) * 120 > MAX_THICKNESS / 2


def test_one_pale_neighbour_does_not_condemn_a_real_gutter():
    """Why the statistic averages the two sides instead of taking the weaker one: a
    white gutter with a near-white tile on one side and content on the other is a real
    separator, and `min` scored it near zero."""
    pitch, h = 120, 300
    img = image(pitch * 2, h, 0)
    img[:, :pitch] = 245           # pale tile, close to the white gutter
    img[:, pitch:] = 30            # dark tile
    img[:, pitch - 2:pitch + 3] = 255
    lab = _to_lab(img)
    line = FakeLine("v", float(pitch), 0.0, float(h - 1), lab[h // 2, pitch])
    assert _flank_contrast(lab, line, pitch) > GRID_FLANK_MIN_DE
