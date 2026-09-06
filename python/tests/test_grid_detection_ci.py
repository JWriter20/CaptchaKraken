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


def _make_low_contrast_grid(n: int, tile: int = 110, gutter: int = 4,
                            tile_value: int = 236) -> str:
    """An NxN grid whose TILES are nearly the same colour as the white gutters.

    This is the shape the consistent-colour tracer cannot walk. Its gutter trace is
    seeded from every row that looks like the gutter, and when the neighbouring
    pixels are almost the gutter's own colour the whole neighbourhood seeds traces
    too; `_merge_lines` then collapses them into one cluster whose support-weighted
    centre sits off the real separator. Measured on the real corpus this is what
    reCAPTCHA 4x4s over open sky do — pure-white gutters at x=103/200/297 came back
    as 66/96/215 — and it is what the colour comb exists to fix.

    A vendor prompt bar is included because it is not decoration: it is what stops a
    vertical gutter's colour run at the top of the grid, and detection has to work
    with the scan band crossing it.
    """
    size = n * tile + (n + 1) * gutter
    canvas = np.full((size + 180, size, 3), 255, dtype=np.uint8)
    canvas[:120] = (200, 120, 40)                     # prompt bar
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
    """A grid whose tiles nearly match its gutters must still be found.

    Regression for the miss the colour comb closes. Before it, this returned None
    for every tile value from 240 down to 220 — the tracer alone cannot separate a
    gutter from a neighbour it cannot tell apart locally.
    """
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
    """A near-uniform image has no grid, however well any lattice drawn on it seals.

    The comb accepts a lattice on weaker CELL evidence once every separator is the
    gutter colour end to end — and a blank-ish canvas seals every lattice you can
    draw on it. What stops it is that its cells do not diverge from the gutter at
    all. This pins that floor; without it the relaxation would hallucinate grids on
    empty frames.
    """
    fd, path = tempfile.mkstemp(suffix="_uniform.png")
    os.close(fd)
    cv2.imwrite(path, make(np.random.default_rng(3)))
    try:
        boxes = find_grid(path)
    finally:
        os.unlink(path)
    assert not boxes, f"find_grid hallucinated a grid on a {name} canvas: {boxes!r}"
