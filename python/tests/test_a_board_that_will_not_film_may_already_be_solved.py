# A recording that catches NOTHING is not a verdict about the board.
#
# `_burst` returns no frames only when EVERY screenshot in its window failed. A still photographs
# fine, so nothing coming back does not mean "this board is static" — it means the widget would not
# screenshot at all, and the commonest reason for that is that it is CLOSING, because the answer was
# accepted.
#
# Every other failure in `_solve_impl` asks `is_captcha_solved` before giving up. This one re-raised.
# Measured: prosopo_grid_3x3 solved 8/8 across six runs on 09-12 and 09-13, then lost four attempts
# on 09-17 to exactly this. Each one had its FIRST board come back from the fixture's own /fx/verify
# graded `solved: true, score 1.0, pred == gt`, and died on the second board the vendor dealt — so the
# gate reported a failure on an attempt whose answers the board had already taken.
#
# The vendors that deal more than one board are where this costs the most: the accepted board buys
# nothing if the solve dies on the next one.
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken.page_solver import (  # noqa: E402
    AnimatedChallengeError, CaptchaSolveError, NothingFilmedError, PageSolver, PageSolverConfig, _now,
)


def _driver(*, rounds_before_blank: int, solved_after: bool, raises=NothingFilmedError,
            max_stale: int = 3):
    """A solver whose Nth round cannot film, and whose widget reports `solved_after` afterwards."""
    solver = PageSolver(config=PageSolverConfig(max_solve_loops=6, post_solve_outcome_timeout_ms=1,
                                                post_solve_delay_ms=1, stale_element_backoff_ms=0,
                                                max_stale_element_retries=max_stale))
    solver._reset_animated_state()
    state = {"rounds": 0, "solved": False}

    def solve_single(page, widget, retry_mode):
        state["rounds"] += 1
        if state["rounds"] == rounds_before_blank + 1:
            state["solved"] = solved_after
            raise raises("could not record the animated challenge (no frame screenshotted)")
        solver._acted_on_board = True
        return True, []

    solver.detect_captcha = lambda page: object()
    solver._solve_single = solve_single
    solver.is_captcha_solved = lambda page: state["solved"]
    solver._banner_kind = lambda page: None
    solver._is_challenge_freshly_rendered = lambda page: False
    return solver, state


def test_a_board_the_vendor_took_is_not_lost_because_the_next_one_would_not_film():
    """The prosopo shape: board 1 accepted, board 2 unscreenshottable, whole solve discarded."""
    solver, state = _driver(rounds_before_blank=1, solved_after=True)
    result = solver._solve_impl(object(), _now(), [])
    assert result.is_solved is True, "an accepted board was thrown away by a failed recording"


def test_the_question_is_asked_before_giving_up():
    solver, state = _driver(rounds_before_blank=1, solved_after=True)
    asked = []
    inner = solver.is_captcha_solved
    solver.is_captcha_solved = lambda page: (asked.append(1), inner(page))[1]
    solver._solve_impl(object(), _now(), [])
    assert asked, "the solve ended without ever asking whether the board had been accepted"


def test_a_board_that_will_not_film_and_is_not_solved_is_re_detected():
    """Not solved is not dead either: the handle is stale for the same reason, so retry it."""
    solver, state = _driver(rounds_before_blank=1, solved_after=False)
    try:
        solver._solve_impl(object(), _now(), [])
    except CaptchaSolveError:
        pass
    assert state["rounds"] > 2, f"gave up after {state['rounds']} rounds instead of re-detecting"


def test_the_retry_is_bounded():
    """Without a bound this is an infinite loop on a widget that never screenshots again."""
    solver, state = _driver(rounds_before_blank=0, solved_after=False, max_stale=2)
    with pytest.raises(CaptchaSolveError):
        solver._solve_impl(object(), _now(), [])
    assert state["rounds"] <= 4, f"re-detected {state['rounds']} times against a bound of 2"


def test_a_first_round_that_cannot_film_still_fails():
    """The recovery is for a board that FOLLOWS an accepted one. A solve that never interacted has
    nothing to protect, and swallowing its failure would hide a widget that never films."""
    solver, state = _driver(rounds_before_blank=0, solved_after=True, max_stale=0)
    with pytest.raises(AnimatedChallengeError):
        solver._solve_impl(object(), _now(), [])


def test_a_genuine_animated_dead_end_is_still_a_failure():
    """`NothingFilmedError` is the narrow case. A board that never settles and cannot be solved from
    keyframes is a real dead end, and must not be quietly retried into the loop ceiling."""
    solver, state = _driver(rounds_before_blank=1, solved_after=True, raises=AnimatedChallengeError)
    with pytest.raises(AnimatedChallengeError):
        solver._solve_impl(object(), _now(), [])


def test_the_narrow_error_is_a_kind_of_the_broad_one():
    """Callers catching AnimatedChallengeError must keep catching this; it is a recording failure."""
    assert issubclass(NothingFilmedError, AnimatedChallengeError)


# ── which of the two errors a failed film raises ────────────────────────────
#
# This is the line the fix turned on, and getting it backwards is not a small mistake: measured on
# the gate, giving a PROVEN animated board the soft landing took hcaptcha_tile_flip_video and
# hcaptcha_item_animal_never_touches from 3 solved and 10 keyframe calls to 0 and 0. The first
# failed film spends `_animated_probe_done`, so every round after it is answered as a still, and the
# gate's own verdict for it is "THE DRIVER NEVER ASKED ABOUT THE KEYFRAMES ... 0 keyframe calls, on
# an animated fixture". A soft landing on the wrong branch is silent; the hard error is not.


def _solver_that_films_nothing(known_animated: bool):
    solver = PageSolver(config=PageSolverConfig())
    solver._reset_animated_state()
    solver._known_animated = known_animated
    solver._grant_video_budget = lambda: None
    solver._burst = lambda element, known=frozenset(), max_ms=None: ([], [], False, 0.0, False)
    return solver


def test_a_board_only_suspected_animated_gets_the_soft_landing():
    """The prosopo shape: a still that did not solve, a speculative film, nothing to catch."""
    solver = _solver_that_films_nothing(known_animated=False)
    with pytest.raises(NothingFilmedError):
        solver._record_keyframes(object())


def test_a_board_proven_animated_still_fails_hard():
    solver = _solver_that_films_nothing(known_animated=True)
    with pytest.raises(AnimatedChallengeError) as caught:
        solver._record_keyframes(object())
    assert not isinstance(caught.value, NothingFilmedError), (
        "a proven animated board took the soft landing; its next round answers as a still and the "
        "keyframes are never asked about"
    )
