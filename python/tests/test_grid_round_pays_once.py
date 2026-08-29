"""Two things the reCAPTCHA grid driver was paying for twice, or not at all.

Both were found by reading the phase budget of a solve that succeeded, which is
why neither had ever shown up as a failure:

    [BUDGET] solve 5.9s — 0.7s useful (11%), 5.3s waiting
    [BUDGET]   grid-load                1.50s  x2      <- paid twice
    [BUDGET]   post-submit-delay        1.48s  x1      <- should not exist
    [BUDGET] * mouse                    0.65s  x0.65

1. THE VERIFY PRESS IS AN INTERACTION, and the grid driver never said so.

   `_solve_recaptcha_grid` sets `performed_action` when it CLICKS A TILE. A
   round that answers `done` — reCAPTCHA 3x3's `none_present` variation, where
   nothing matches and the control reads SKIP, and equally any board the model
   reads as already correct — clicks no tile, presses Verify, and returns
   False.

   The caller reads that False as "this round did nothing", which costs two
   things:

     - it takes the `else` branch and sleeps `post_solve_delay_ms` flat
       (1200-1500ms) instead of POLLING for the vendor's verdict, which
       measured 3-528ms on a success. ~1s of dead time on every such solve.
     - if the widget has not finished tearing down by the time it looks, it
       raises "captcha still detected but the solver performed no
       interactions" — an outright failure on a correctly submitted answer.

   This is the same bug `test_empty_answer_still_submits.py` pins in
   `_solve_single`, in the other driver. That one was fixed and this one was
   not, because the two drivers were fixed on different days.

2. THE GRID-LOAD WAIT WAS PAID TWICE, BACK TO BACK.

   `_solve_single` waits for the cells to load, reads the grid boxes, sees a
   3x3 and hands over to `_solve_recaptcha_grid` — whose round 1 opens by
   waiting for the cells to load again, on a board that has not been touched
   since. Measured 2148ms x2 on one solve. Rounds 2..N still wait, because by
   then this driver has clicked tiles and the board really is reloading.
"""

from __future__ import annotations

from typing import Any, Dict, List

# The fakes: a PageSolver with everything around the grid driver stubbed out.
# Imported rather than copied — they are 130 lines and there must be one
# description of what a fake reCAPTCHA board does.
from test_recaptcha_chipped_board import (  # noqa: E402
    CELL_5,
    CLICK_5,
    DONE,
    ELEMENT_BOX,
    GRID_ARG,
    _driver,
    _FakeElement,
    _FakePage,
)

#: A board that ticked our click and kept the photo — nothing to wait for.
CHIPPED = {"empty": [], "changing": [5], "loaded": [1, 2, 3, 4, 6, 7, 8, 9], "selected": [5]}
#: A board that blanked the clicked cell on its way to a replacement.
SWAPPING = {"empty": [5], "changing": [], "loaded": [1, 2, 3, 4, 6, 7, 8, 9], "selected": []}


def _counting_driver(states: Any, answers: List[Dict[str, Any]]):
    """`_driver`, plus a count of how many times the grid-load wait was paid."""
    solver, log = _driver(states, answers)
    log["grid_loads"] = 0

    def wait(_element: Any) -> bool:
        log["grid_loads"] += 1
        return True

    solver._wait_for_grid_cells_loaded = wait  # type: ignore[method-assign]
    return solver, log


def _solve(solver: Any):
    return solver._solve_recaptcha_grid(
        _FakePage(), _FakeElement(), None, GRID_ARG, dict(ELEMENT_BOX)
    )


# ── 1. the submit ──────────────────────────────────────────────────────────

def test_a_round_that_only_presses_verify_still_reports_an_interaction():
    """`done` on round 1: no tile clicked, Verify pressed, answer submitted."""
    solver, log = _counting_driver(CHIPPED, [DONE])
    performed, _ = _solve(solver)

    assert log["clicked"] == [], "the model said `done`; nothing should be clicked"
    assert log["submits"] == 1, "a `done` answer is submitted by pressing Verify"
    assert performed is True, (
        "the driver pressed Verify and reported that it did nothing. The caller "
        "then sleeps post_solve_delay_ms instead of polling for the verdict "
        "(~1s of dead time), and raises 'performed no interactions' if the "
        "widget has not vanished yet — on an answer that was correctly sent."
    )


def test_a_round_that_clicks_and_submits_still_reports_an_interaction():
    """The case that already worked, kept so the fix cannot be a blanket True."""
    solver, log = _counting_driver(CHIPPED, [CLICK_5, DONE])
    performed, _ = _solve(solver)

    assert log["clicked"] == [CELL_5]
    assert performed is True


def test_a_round_cap_exit_reports_no_interaction():
    """The guard against 'just return True'.

    A board that keeps answering `wait` never submits and never clicks, so it
    genuinely did nothing — and the caller's infinite-loop guard is the only
    thing that ends it. Reporting an interaction there would re-arm a solve
    that cannot progress.
    """
    solver, log = _counting_driver(SWAPPING, [{"action": "wait"}])
    performed, _ = _solve(solver)

    assert log["submits"] == 0, "nothing was answered, so nothing may be submitted"
    assert performed is False


# ── 2. the wait ────────────────────────────────────────────────────────────

def test_round_one_inherits_the_callers_grid_load_wait():
    """One round, one wait — and round 1's was already paid by `_solve_single`.

    That caller waits for the cells, reads the grid boxes off the loaded board
    and only then hands over here. Waiting again is a poll interval and two
    screenshots spent re-establishing something nothing has changed since.
    """
    solver, log = _counting_driver(CHIPPED, [DONE])
    _solve(solver)

    assert log["grid_loads"] == 0, (
        f"round 1 waited for the grid to load {log['grid_loads']} time(s); the "
        f"caller has just done exactly that and nothing has touched the board "
        f"in between"
    )


def test_later_rounds_still_wait_for_the_board_they_changed():
    """Rounds 2..N must keep waiting — they clicked, so tiles ARE reloading."""
    solver, log = _counting_driver(SWAPPING, [CLICK_5, DONE])
    _solve(solver)

    assert log["rounds"] == 2, "a replaced tile has to be read once it lands"
    assert log["grid_loads"] == 1, (
        "round 2 opens on a board this driver has just clicked, so it must wait "
        "for the replacement to paint before the model reads it"
    )
