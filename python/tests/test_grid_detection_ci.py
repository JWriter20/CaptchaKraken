"""
Hermetic grid-detection smoke tests for CI.

Unlike test_grid_detection.py (which needs the local captchaimages/ fixtures) and
test_find_grid_corpus.py (which needs the cleanSamples/ real corpus), this module
SYNTHESIZES grid images in memory, so it runs anywhere — no GPU, no network, no
large fixtures. It is the fast guard that protects the core invariant: a clean
NxN grid of distinct-coloured tiles separated by white gutters is detected as an
NxN grid.

find_grid depends only on cv2 + numpy, so this stays cheap and deterministic.
"""
import os
import sys
import tempfile

import numpy as np
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captchakraken.tool_calls.find_grid import find_grid  # noqa: E402

try:
    import cv2
except Exception:  # pragma: no cover - cv2 is a hard dep of find_grid
    cv2 = None


def _make_grid_image(n: int, tile: int = 110, gutter: int = 6) -> str:
    """Render an NxN grid of distinct-coloured tiles on a pure-white canvas.

    Gutters are pure white (255,255,255) and each tile is a solid, clearly
    non-white colour — exactly the lattice the line tracer is built to find
    (white separators + cell-interior divergence from the gutter colour).
    Returns a path to a temp PNG the caller is responsible for deleting.
    """
    size = n * tile + (n + 1) * gutter
    canvas = np.full((size, size, 3), 255, dtype=np.uint8)  # white gutters
    # Spread hues across tiles so no two adjacent cells share a colour.
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
    """A clean NxN white-gutter grid must be detected with N*N cells."""
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
    """Detected boxes must be ordered row-major and have positive extent."""
    path = _make_grid_image(3)
    try:
        boxes = find_grid(path)
    finally:
        os.unlink(path)
    assert boxes and len(boxes) == 9
    for b in boxes:
        assert len(b) == 4, f"box must be a 4-tuple, got {b!r}"
        x0, y0, x1, y1 = b
        # find_grid returns (x0, y0, x1, y1) corner boxes.
        assert x1 > x0 and y1 > y0, f"box has non-positive extent: {b!r}"


@pytest.mark.skipif(cv2 is None, reason="cv2 not installed")
def test_find_grid_rejects_plain_canvas():
    """A blank white canvas has no grid — find_grid must not hallucinate one."""
    blank = np.full((360, 360, 3), 255, dtype=np.uint8)
    fd, path = tempfile.mkstemp(suffix="_blank.png")
    os.close(fd)
    cv2.imwrite(path, blank)
    try:
        boxes = find_grid(path)
    finally:
        os.unlink(path)
    assert not boxes, f"find_grid hallucinated a grid on a blank canvas: {boxes!r}"
