"""A chosen lattice must separate something: a video keyframe once produced a confident 12-cell grid over smooth teal.

Probe distances scale with the pitch (hCaptcha gutters are ~13px), the statistic averages both flanks because
`min` put real grids below the false positives, and the gate runs on the chosen grid only (per-line it added FPs 2 -> 4 -> 6).
"""

import os
import sys

import numpy as np
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captchakraken.tool_calls.find_grid import (
    FLANK_PITCH_FRACS,
    GRID_FLANK_MIN_DE,
    MAX_THICKNESS,
    _flank_contrast,
    _to_lab,
)


class FakeLine:

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
    w, h, pitch = 240, 300, 120
    img = image(w, h, 200)
    lab = _to_lab(img)
    line = FakeLine("v", float(pitch), 0.0, float(h - 1), lab[h // 2, pitch])
    assert _flank_contrast(lab, line, pitch) < GRID_FLANK_MIN_DE


@pytest.mark.parametrize("gutter_px", [4, 13, 20, MAX_THICKNESS + 6])
def test_a_wide_gutter_is_still_measured_against_the_cells(gutter_px):
    """The hCaptcha regression: a fixed-offset probe sat inside gutters this wide and reported no contrast."""
    lab, line, pitch = vertical_gutter(gutter_px)
    assert _flank_contrast(lab, line, pitch) > GRID_FLANK_MIN_DE, (
        f"a {gutter_px}px gutter was measured against itself, not against its cells")


def test_the_probe_clears_any_gutter_the_tracer_would_accept():
    assert min(FLANK_PITCH_FRACS) * 120 > MAX_THICKNESS / 2


def test_one_pale_neighbour_does_not_condemn_a_real_gutter():
    """Why mean, not min: a white gutter with one near-white tile is a real separator, and `min` scored it near zero."""
    pitch, h = 120, 300
    img = image(pitch * 2, h, 0)
    img[:, :pitch] = 245
    img[:, pitch:] = 30
    img[:, pitch - 2:pitch + 3] = 255
    lab = _to_lab(img)
    line = FakeLine("v", float(pitch), 0.0, float(h - 1), lab[h // 2, pitch])
    assert _flank_contrast(lab, line, pitch) > GRID_FLANK_MIN_DE
