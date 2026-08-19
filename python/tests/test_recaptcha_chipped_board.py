"""
Regression: a reCAPTCHA board that TICKS a clicked tile is already answered.

reCAPTCHA replies to a click in one of exactly two ways, and which one it picks
is the entire difference between the two kinds of board:

  * the small blue CHIP in the tile's top-left corner — the widget KEPT the
    photo. Nothing more is coming; the selection IS the answer and the only
    thing left to do is press Verify.
  * the large blue CHECK across the middle of the tile — the widget is SWAPPING
    that photo out. What arrives underneath may match as well, so the board has
    to be read again, which is what the multi-round driver is for.

A widget that swaps one clicked cell swaps them all, so a chip and a centred
check are never on one board at once — the finetune repo's 3x3 generator says so
in prose and pins it on every board it draws
(`tests/test_recaptcha_3x3_clicked_states.py`). One look at the tiles we just
clicked therefore settles which board this is, and `detect_selected_cells` can
already tell the two apart: the chip is what it reports as `selected`, and it
looks for it only in the top-left corner, with a circularity and a centroid test
that a centred check fails.

The driver used to watch only for the swap. On a chipping board the chip's
ARRIVAL — the photo zooms out, a blue disc appears — reads as `changing`, i.e.
exactly like the first frame of a swap, so the driver waited for a replacement
that was never coming and then spent a second inference to be told the board was
`done`. One wasted model call on every static reCAPTCHA, 3x3 and 4x4 alike.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from captchakraken.page_solver import (  # noqa: E402
    PageSolver,
    PageSolverConfig,
    _GridSession,
)

# 3x3, 100px cells, in screenshot pixel space.
GRID: List[List[int]] = [
    [0, 0, 100, 100], [100, 0, 200, 100], [200, 0, 300, 100],
    [0, 100, 100, 200], [100, 100, 200, 200], [200, 100, 300, 200],
    [0, 200, 100, 300], [100, 200, 200, 300], [200, 200, 300, 300],
]
ELEMENT_BOX = {"x": 0.0, "y": 0.0, "width": 300.0, "height": 300.0}
GRID_ARG = {"boxes": GRID, "size": 3, "screenshot_w": 300, "screenshot_h": 300}
# Normalised centre of the middle cell -> cell 5.
CELL_5 = [0.4, 0.4, 0.6, 0.6]

CLICK_5 = {"action": "click", "target_bounding_boxes": [CELL_5]}
DONE = {"action": "done"}


class _FakeFrame:
    """Only ever handed to the stubbed verify-button lookup."""


class _FakeElement:
    def content_frame(self) -> _FakeFrame:
        return _FakeFrame()

    def bounding_box(self) -> Dict[str, float]:
        return dict(ELEMENT_BOX)


class _FakePage:
    """The driver only reaches the page through stubbed mouse helpers."""


def _driver(states: Optional[Dict[str, List[int]]], answers: List[Dict[str, Any]]):
    """A PageSolver with everything around the grid driver stubbed out.

    `states` is what the CV layer reports on every poll, `answers` one model
    answer per round. The returned log counts the two things this file is about:
    how many inferences the board cost, and whether it was submitted.
    """
    # The REAL __init__ (a truthy sentinel keeps it from building a live
    # CaptchaSolver), so per-solve state added there cannot go missing here.
    solver = PageSolver(
        config=PageSolverConfig(
            # The real windows are seconds long and this test has no browser to
            # wait for; the decisions under test are unaffected by how long the
            # poll takes.
            recaptcha_fade_onset_grace_ms=60,
            recaptcha_dynamic_fade_poll_ms=1,
            recaptcha_dynamic_fade_wait_ms=20,
        ),
        solver=object(),
    )
    solver._solver = None
    solver._cursor_seeded = True

    log: Dict[str, Any] = {"rounds": 0, "clicked": [], "waits": 0, "submits": 0}

    def solution(_path: str, *_a: Any, **_k: Any):
        answer = answers[min(log["rounds"], len(answers) - 1)]
        log["rounds"] += 1
        return [answer], [{"total_tokens": 1}]

    def wait_loaded(*_a: Any) -> bool:
        log["waits"] += 1
        return True

    def submit(_page: Any, _button: Any) -> None:
        log["submits"] += 1

    solver._wait_for_grid_cells_loaded = lambda _el: True  # type: ignore[method-assign]
    solver._screenshot = lambda *a, **k: None              # type: ignore[method-assign]
    solver._grid_cell_states = lambda *_a: states          # type: ignore[method-assign]
    solver._hover_cell = lambda *_a: None                  # type: ignore[method-assign]
    solver._frame_changed_since = lambda *_a: False        # type: ignore[method-assign]
    solver._get_solution = solution                        # type: ignore[method-assign]
    solver._execute_click = (                              # type: ignore[method-assign]
        lambda _page, action, _box: log["clicked"].append(action["target_bounding_box"])
    )
    solver._wait_for_any_clicked_tile_loaded = wait_loaded  # type: ignore[method-assign]
    solver._get_verify_button = lambda _frame: "verify"     # type: ignore[method-assign]
    solver._move_and_click = submit                         # type: ignore[method-assign]
    return solver, log


def _session() -> _GridSession:
    return _GridSession(
        grid_boxes=GRID, element_box=dict(ELEMENT_BOX),
        scale_x=1.0, scale_y=1.0, screenshot_w=300, screenshot_h=300,
    )


def _solve(solver: PageSolver):
    return solver._solve_recaptcha_grid(
        _FakePage(), _FakeElement(), None, GRID_ARG, dict(ELEMENT_BOX)
    )


def test_a_chipped_tile_submits_without_a_second_inference():
    # The board ticked cell 5 and kept the photo: `selected` names it, and it
    # also reads as `changing` because the chip is still animating in — which is
    # what used to be mistaken for the first frame of a swap.
    solver, log = _driver(
        {"empty": [], "changing": [5], "loaded": [1, 2, 3, 4, 6, 7, 8, 9], "selected": [5]},
        [CLICK_5, DONE],
    )
    performed, usage = _solve(solver)

    assert log["rounds"] == 1, "a ticked board must not be read a second time"
    assert log["clicked"] == [CELL_5]
    assert log["waits"] == 0, "nothing is being replaced, so there is nothing to wait for"
    assert log["submits"] == 1
    assert performed is True
    assert usage == [{"total_tokens": 1}]


def test_a_swapping_tile_still_costs_another_round():
    # The other board: cell 5 blanked to white on its way to a new photo, so the
    # answer is not known yet and the driver must look again.
    solver, log = _driver(
        {"empty": [5], "changing": [], "loaded": [1, 2, 3, 4, 6, 7, 8, 9], "selected": []},
        [CLICK_5, DONE],
    )
    performed, _ = _solve(solver)

    assert log["rounds"] == 2, "a replaced tile has to be read once it lands"
    assert log["waits"] == 1
    assert log["submits"] == 1
    assert performed is True


def test_one_chip_among_the_clicked_tiles_is_not_a_verdict():
    # All-or-nothing, on purpose. The two states never share a board, so a
    # partial reading is a MISREAD, and the two mistakes do not cost the same:
    # calling a swapping board finished submits half an answer and burns the
    # attempt, while calling a ticked board unfinished costs one inference.
    solver, _ = _driver(
        {"empty": [], "changing": [5, 6], "loaded": [], "selected": [5]}, [],
    )
    loading, chipped = solver._watch_clicked_tiles(
        _FakePage(), _FakeElement(), _session(), [5, 6]
    )
    assert chipped is False
    assert loading == [5, 6]


def test_a_chip_on_a_tile_we_did_not_click_is_not_a_verdict():
    # Only the tiles this round clicked can answer the question. A chip
    # elsewhere is a click from an earlier round (or the user's own), and says
    # nothing about what the widget just did with ours.
    solver, _ = _driver(
        {"empty": [], "changing": [], "loaded": [5], "selected": [2]}, [],
    )
    _, chipped = solver._watch_clicked_tiles(
        _FakePage(), _FakeElement(), _session(), [5]
    )
    assert chipped is False
