"""A Verify press is an interaction: a `done` round that returned False paid a flat post_solve_delay sleep and then aborted as "performed no interactions". The grid-load wait was paid twice (2148ms x2). The round-cap exit must still report none."""

from __future__ import annotations

from typing import Any, Dict, List

from test_recaptcha_chipped_board import (
    CELL_5,
    CLICK_5,
    DONE,
    ELEMENT_BOX,
    GRID_ARG,
    _driver,
    _FakeElement,
    _FakePage,
)

CHIPPED = {"empty": [], "changing": [5], "loaded": [1, 2, 3, 4, 6, 7, 8, 9], "selected": [5]}
SWAPPING = {"empty": [5], "changing": [], "loaded": [1, 2, 3, 4, 6, 7, 8, 9], "selected": []}


def _counting_driver(states: Any, answers: List[Dict[str, Any]]):
    solver, log = _driver(states, answers)
    log["grid_loads"] = 0

    def wait(_element: Any) -> bool:
        log["grid_loads"] += 1
        return True

    solver._wait_for_grid_cells_loaded = wait
    return solver, log


def _solve(solver: Any):
    return solver._solve_recaptcha_grid(
        _FakePage(), _FakeElement(), None, GRID_ARG, dict(ELEMENT_BOX)
    )


def test_a_round_that_only_presses_verify_still_reports_an_interaction():
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
    solver, log = _counting_driver(CHIPPED, [CLICK_5, DONE])
    performed, _ = _solve(solver)

    assert log["clicked"] == [CELL_5]
    assert performed is True


def test_a_round_cap_exit_reports_no_interaction():
    solver, log = _counting_driver(SWAPPING, [{"action": "wait"}])
    performed, _ = _solve(solver)

    assert log["submits"] == 0, "nothing was answered, so nothing may be submitted"
    assert performed is False


def test_round_one_inherits_the_callers_grid_load_wait():
    solver, log = _counting_driver(CHIPPED, [DONE])
    _solve(solver)

    assert log["grid_loads"] == 0, (
        f"round 1 waited for the grid to load {log['grid_loads']} time(s); the "
        f"caller has just done exactly that and nothing has touched the board "
        f"in between"
    )


def test_later_rounds_still_wait_for_the_board_they_changed():
    solver, log = _counting_driver(SWAPPING, [CLICK_5, DONE])
    _solve(solver)

    assert log["rounds"] == 2, "a replaced tile has to be read once it lands"
    assert log["grid_loads"] == 1, (
        "round 2 opens on a board this driver has just clicked, so it must wait "
        "for the replacement to paint before the model reads it"
    )
