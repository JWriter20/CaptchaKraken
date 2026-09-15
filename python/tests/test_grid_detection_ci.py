"""Hermetic synthetic grids: the fast guard that a clean NxN white-gutter grid is detected as NxN, with no corpus."""

import os
import sys
import tempfile

import numpy as np
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captchakraken.tool_calls.find_grid import find_grid

try:
    import cv2
except Exception:
    cv2 = None


def _make_grid_image(n: int, tile: int = 110, gutter: int = 6) -> str:
    size = n * tile + (n + 1) * gutter
    canvas = np.full((size, size, 3), 255, dtype=np.uint8)
    for r in range(n):
        for c in range(n):
            idx = r * n + c
            hue = int((idx * 180 / (n * n)) % 180)
            hsv = np.uint8([[[hue, 200, 200]]])
            bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0].tolist()
            y0 = gutter + r * (tile + gutter)
            x0 = gutter + c * (tile + gutter)
            canvas[y0:y0 + tile, x0:x0 + tile] = bgr
    fd, path = tempfile.mkstemp(suffix=f"_grid{n}x{n}.png")
    os.close(fd)
    cv2.imwrite(path, canvas)
    return path


@pytest.mark.skipif(cv2 is None, reason="cv2 not installed")
@pytest.mark.parametrize("n", [3, 4])
def test_find_grid_detects_clean_nxn(n):
    path = _make_grid_image(n)
    try:
        boxes = find_grid(path)
    finally:
        os.unlink(path)
    assert boxes is not None, f"find_grid returned None for a clean {n}x{n} grid"
    assert len(boxes) == n * n, (
        f"expected {n * n} cells for a {n}x{n} grid, got {len(boxes)}"
    )


@pytest.mark.skipif(cv2 is None, reason="cv2 not installed")
def test_find_grid_boxes_are_well_formed():
    path = _make_grid_image(3)
    try:
        boxes = find_grid(path)
    finally:
        os.unlink(path)
    assert boxes and len(boxes) == 9
    for b in boxes:
        assert len(b) == 4, f"box must be a 4-tuple, got {b!r}"
        x0, y0, x1, y1 = b
        assert x1 > x0 and y1 > y0, f"box has non-positive extent: {b!r}"


@pytest.mark.skipif(cv2 is None, reason="cv2 not installed")
def test_find_grid_rejects_plain_canvas():
    blank = np.full((360, 360, 3), 255, dtype=np.uint8)
    fd, path = tempfile.mkstemp(suffix="_blank.png")
    os.close(fd)
    cv2.imwrite(path, blank)
    try:
        boxes = find_grid(path)
    finally:
        os.unlink(path)
    assert not boxes, f"find_grid hallucinated a grid on a blank canvas: {boxes!r}"


def _make_low_contrast_grid(n: int, tile: int = 110, gutter: int = 4,
                            tile_value: int = 236) -> str:
    size = n * tile + (n + 1) * gutter
    canvas = np.full((size + 180, size, 3), 255, dtype=np.uint8)
    canvas[:120] = (200, 120, 40)
    rng = np.random.default_rng(7)
    for r in range(n):
        for c in range(n):
            v = np.clip(tile_value + rng.integers(-6, 7, size=3), 0, 255).astype(np.uint8)
            y0 = 120 + gutter + r * (tile + gutter)
            x0 = gutter + c * (tile + gutter)
            canvas[y0:y0 + tile, x0:x0 + tile] = v
            canvas[y0 + 8:y0 + tile - 8, x0 + 8:x0 + tile - 8] = np.clip(
                v.astype(int) - 6, 0, 255).astype(np.uint8)
    fd, path = tempfile.mkstemp(suffix=f"_lowcontrast{n}x{n}.png")
    os.close(fd)
    cv2.imwrite(path, canvas)
    return path


@pytest.mark.skipif(cv2 is None, reason="cv2 not installed")
@pytest.mark.parametrize("n", [3, 4])
def test_find_grid_detects_low_contrast_gutters(n):
    """The miss the colour comb closes: before it, this returned None for every tile value from 240 down to 220."""
    path = _make_low_contrast_grid(n)
    try:
        boxes = find_grid(path)
    finally:
        os.unlink(path)
    assert boxes is not None, (
        f"find_grid returned None for a low-contrast {n}x{n} grid — the comb cue "
        f"is not firing"
    )
    assert len(boxes) == n * n, f"expected {n * n} cells, got {len(boxes)}"


@pytest.mark.skipif(cv2 is None, reason="cv2 not installed")
@pytest.mark.parametrize("name,make", [
    ("near-white noise", lambda r: np.clip(
        np.full((580, 400, 3), 250, int) + r.integers(-3, 4, (580, 400, 3)),
        0, 255).astype(np.uint8)),
    ("smooth gradient", lambda r: np.clip(
        np.linspace(238, 255, 580)[:, None, None] + np.zeros((580, 400, 3)),
        0, 255).astype(np.uint8)),
])
def test_find_grid_rejects_near_uniform_canvas(name, make):
    """A near-uniform canvas seals every lattice drawn on it; the cell-divergence floor stops the seal relaxation hallucinating grids."""
    fd, path = tempfile.mkstemp(suffix="_uniform.png")
    os.close(fd)
    cv2.imwrite(path, make(np.random.default_rng(3)))
    try:
        boxes = find_grid(path)
    finally:
        os.unlink(path)
    assert not boxes, f"find_grid hallucinated a grid on a {name} canvas: {boxes!r}"
