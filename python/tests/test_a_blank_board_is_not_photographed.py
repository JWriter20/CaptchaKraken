"""A still board is not a loaded board.

Every gate in front of the inference screenshot asks whether the widget has
stopped CHANGING. The blank panel a vendor shows while it rebuilds the board
answers that with a confident yes — it is the stillest the widget ever is — so
the solver photographs the hole and asks the model to solve it. The model
answers the only way it can, with the middle of the image.

MEASURED on gt4.geetest.com's slide demo, 2026-09-12, ten live attempts with
every request banked through a proxy in front of vLLM:

    52 requests, 6 of them a panel with no puzzle in it
    every one of the 6 came back `to:[480,310]`-ish — dead centre
    attempt 1  the driver EXECUTED one: a drag to 48.5%, the round burnt
    attempt 2  ended "solver performed no interactions" on a blank board

The pairing that catches this today is `_wait_for_hcaptcha_challenge_images`,
which asks the DOM and so only knows hCaptcha. `_wait_for_board_painted` asks
the picture, and holds for GeeTest and everything else.

The JS half is js/src/board-paint-gate.test.ts.
"""
import os
import sys
import tempfile

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from captchakraken.page_solver import PageSolver, PageSolverConfig          # noqa: E402
from captchakraken.tool_calls.board_painted import (                        # noqa: E402
    TEXTURE_FLOOR, board_is_painted, centre_texture,
)

cv2 = pytest.importorskip("cv2")


def _png(pixels) -> str:
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    cv2.imwrite(path, pixels)
    return path


def test_a_flat_panel_carries_no_texture():
    """The blank board, as the widget actually draws it: one colour."""
    path = _png(np.full((400, 340), 255, dtype=np.uint8))
    try:
        assert centre_texture(path) == 0.0
        assert board_is_painted(path)["painted"] is False
    finally:
        os.unlink(path)


def test_chrome_alone_does_not_count_as_a_puzzle():
    """A caption above and an icon row below paint EARLY, and paint on a blank
    board too. Measuring the whole panel would read them as content and wave the
    hole through, which is why the measure is taken from the middle."""
    img = np.full((400, 340), 255, dtype=np.uint8)
    img[10:40, 40:300] = 0        # caption
    img[360:390, 20:120] = 0      # footer icons
    path = _png(img)
    try:
        assert board_is_painted(path)["painted"] is False
    finally:
        os.unlink(path)


def test_a_pale_puzzle_still_reads_as_painted():
    """The floor is set for blank-versus-anything, not for any puzzle's density.
    Line art on white — geetest_v4_svg — must not be mistaken for a reset."""
    img = np.full((400, 340), 255, dtype=np.uint8)
    for x in range(60, 280, 24):
        img[120:280, x:x + 2] = 20          # thin strokes, ~2.4% of the centre
    path = _png(img)
    try:
        assert centre_texture(path) > TEXTURE_FLOOR
        assert board_is_painted(path)["painted"] is True
    finally:
        os.unlink(path)


def test_an_unreadable_image_is_not_a_blank_one():
    assert board_is_painted("/nonexistent/board.png")["painted"] is None


class _Element:
    """A widget that paints its puzzle after `blank_for` grabs."""

    def __init__(self, blank_for: int, throw_first: bool = False):
        self.blank_for = blank_for
        self.throw_first = throw_first
        self.grabs = 0

    def screenshot(self, **kw):
        self.grabs += 1
        if self.throw_first and self.grabs == 1:
            raise RuntimeError("element is detaching")
        img = np.full((400, 340), 255, dtype=np.uint8)
        if self.grabs > self.blank_for:
            img[110:290, 50:290] = np.random.default_rng(0).integers(
                0, 255, (180, 240), dtype=np.uint8)
        cv2.imwrite(kw["path"], img)


def _solver(**cfg):
    s = PageSolver(config=PageSolverConfig(
        board_paint_poll_ms=1, board_paint_timeout_ms=80, **cfg))
    s._screenshot = lambda el, path, **kw: el.screenshot(path=path)   # type: ignore
    return s


def test_a_painted_board_is_photographed_at_once():
    s, el = _solver(), _Element(blank_for=0)
    assert s._wait_for_board_painted(el) < 80
    assert el.grabs == 1


def test_a_blank_board_is_polled_until_it_paints():
    s, el = _solver(), _Element(blank_for=3)
    s._wait_for_board_painted(el)
    assert el.grabs == 4, "should have kept looking until there was a puzzle"


def test_a_board_that_never_paints_falls_through():
    """A gate that can refuse to ever take a picture turns one wasted round into
    a dead solve. Bounded, and then proceed."""
    s, el = _solver(), _Element(blank_for=10_000)
    waited = s._wait_for_board_painted(el)
    assert waited < 2_000, f"must be bounded by board_paint_timeout_ms, waited {waited}ms"


def test_a_screenshot_that_throws_is_a_skipped_poll():
    s, el = _solver(), _Element(blank_for=0, throw_first=True)
    s._wait_for_board_painted(el)
    assert el.grabs == 2, "one flaky grab must not wave a blank board through"


def test_the_freshness_resolve_waits_for_paint():
    """The path that most needs the gate, and the one it was missing.

    `_solve_frame_freshness_guarded` re-queries when the frame changed during
    inference — exactly the moment a vendor that rebuilds its panel between
    rounds is showing the blank. MEASURED 2026-09-12 with the gate on the main
    screenshot only: 2 of 12 live requests still carried a board with no puzzle
    in it, and both were `freshsolve` frames.
    """
    src = open(os.path.join(os.path.dirname(__file__), "..", "src",
                            "captchakraken", "page_solver.py")).read()
    at = src.index('_tmp_png("freshsolve")')
    assert "self._wait_for_board_painted(element)" in src[max(0, at - 1200):at], (
        "the freshness re-solve must wait for the board to paint before grabbing "
        "a fresh frame — it fires precisely when the board is mid-rebuild")
