import os
import sys
import tempfile

import numpy as np
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captchakraken.tool_calls.find_grid import find_grid

cv2 = pytest.importorskip("cv2")

LEFT, TOP, TILE_W, TILE_H, GUTTER, WIDTH = 16, 60, 94, 79, 8, 340
GRID_BOTTOM = TOP + 3 * TILE_H + 2 * GUTTER


def _widget(height):
    canvas = np.full((height, WIDTH, 3), 255, dtype=np.uint8)
    rng = np.random.default_rng(7)
    for r in range(3):
        for c in range(3):
            y, x = TOP + r * (TILE_H + GUTTER), LEFT + c * (TILE_W + GUTTER)
            hue = int((r * 3 + c) * 20)
            base = cv2.cvtColor(np.uint8([[[hue, 200, 170]]]), cv2.COLOR_HSV2BGR)[0, 0]
            patch = np.full((TILE_H, TILE_W, 3), base, dtype=np.int16)
            patch += rng.integers(-18, 19, (TILE_H, TILE_W, 3), dtype=np.int16)
            canvas[y:y + TILE_H, x:x + TILE_W] = np.clip(patch, 0, 255).astype(np.uint8)
    cv2.rectangle(canvas, (0, 0), (WIDTH, 4), (230, 120, 40), -1)
    cv2.rectangle(canvas, (LEFT, 26), (LEFT + 150, 40), (70, 70, 70), -1)
    cv2.circle(canvas, (WIDTH - 30, 33), 11, (40, 40, 40), -1)
    for i in range(4):
        x0 = 36 + i * 42
        cv2.rectangle(canvas, (x0, 328), (x0 + 22, 338), (115, 115, 115), -1)
    cv2.rectangle(canvas, (240, 350), (322, 360), (135, 135, 135), -1)
    cv2.rectangle(canvas, (36, 372), (120, 382), (150, 150, 150), -1)
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    cv2.imwrite(path, canvas)
    return path


def _boxes(height):
    path = _widget(height)
    try:
        return find_grid(path)
    finally:
        os.unlink(path)


def test_a_short_widget_still_reports_its_3x3():
    boxes = _boxes(385)
    assert boxes is not None, "no grid detected on a 3x3 above an ordinary footer"
    assert len(boxes) == 9, f"expected a 3x3, got {len(boxes)} cells"


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN, UNFIXED. Where the fourth row FITS the canvas nothing downstream "
    "rejects it: the cells it invents over the chrome are mostly the gutter "
    "colour, and SEALED_DIVERGE_TOL (2.0) is a low enough content bar for them "
    "to pass. The pitch fix does not reach this — here the board's bottom border "
    "is corroborated honestly, because the perpendicular lines really do continue "
    "a full cell below it, through the footer. Fixing it means either a content "
    "bar that can tell a chrome row from a tile row or a corroboration that stops "
    "at the board, both of which need measuring against the real corpus first. "
    "Left failing rather than deleted so the exposure is recorded: this shape "
    "returns a 4x3 that is not there, and callers that check the cell count see "
    "'no grid' instead."))
def test_a_tall_widget_does_not_grow_a_row_into_the_footer():
    boxes = _boxes(430)
    assert boxes is not None, "no grid detected"
    assert len(boxes) == 9, f"the footer was read as a fourth row: {len(boxes)} cells"
    assert max(b[3] for b in boxes) <= GRID_BOTTOM + GUTTER, "a cell reaches into the footer"
