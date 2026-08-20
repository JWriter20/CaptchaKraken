"""Regression: a submit that was already ACCEPTED must not be re-solved, and a
closed page must not be retried as if it were a stale handle.

Both bugs were found driving the reCAPTCHA demo headed. The sequence is:

  loop 2  clicked tiles -> clicked Verify
  loop 3  stale challenge handle after submit; re-detecting next round (1/3)
          TargetClosedError: ElementHandle.screenshot: Target ... has been closed

reCAPTCHA tears the challenge iframe down the instant it accepts an answer, so
the handle we still hold goes stale. That staleness means "you already won", but
the driver treated it as "the page moved on" and looped back to re-detect,
re-screenshot and re-solve a puzzle that no longer existed. Headless usually won
that race; headed usually lost it.

Two distinct defects, one symptom:

1. The retry branch spent another round before ever asking whether the vendor had
   already signalled solved. The top-of-loop check exists, but it only runs after
   the backoff, and it does DOM reads of its own — so on a torn-down target it
   raised instead of answering.
2. `_STALE_HANDLE_RE` matched "Target closed", so a genuinely dead page was
   classified as a recoverable stale handle and retried into three times, and the
   error that finally surfaced named whatever call happened to run last
   (`ElementHandle.screenshot`) rather than the real cause.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from captchakraken.page_solver import (  # noqa: E402
    _CLOSED_TARGET_RE,
    _STALE_HANDLE_RE,
    PageClosedError,
)

# Verbatim Playwright messages seen in the failing headed runs.
CLOSED_MESSAGES = [
    "ElementHandle.screenshot: Target page, context or browser has been closed",
    "Page.query_selector: Target page, context or browser has been closed",
    "Target closed",
    "Session closed. Most likely the page has been closed.",
]

# Genuine mid-transition staleness — hCaptcha swapping in the next round.
STALE_MESSAGES = [
    "element is not attached to the DOM",
    "element is not visible",
    "Timeout 5000ms exceeded",
    "elementHandle.screenshot: element is detached from document",
]


@pytest.mark.parametrize("message", CLOSED_MESSAGES)
def test_closed_target_is_not_classified_as_stale(message):
    """A closed page is terminal. Classifying it as staleness burns the retry
    budget on a target that can never answer, and hides the cause."""
    assert _CLOSED_TARGET_RE.search(message), f"should read as closed: {message!r}"
    assert not _STALE_HANDLE_RE.search(message), (
        f"{message!r} must NOT be retried as a stale handle — it means the page is gone"
    )


@pytest.mark.parametrize("message", STALE_MESSAGES)
def test_real_staleness_still_retries(message):
    """The original behaviour has to survive the fix: an iframe swap mid-solve
    is a transition, not a failure."""
    assert _STALE_HANDLE_RE.search(message), f"should read as stale: {message!r}"
    assert not _CLOSED_TARGET_RE.search(message)


def _solver(failure: str, *, solved_after_failure: bool):
    """A driver wired to reproduce the exact live sequence.

    Round 1 interacts and submits. The vendor has not answered yet, so the
    post-submit poll times out and the loop goes round again. Round 2's
    interaction raises `failure` — the handle is stale (or the target is gone)
    because the vendor has just accepted and torn the frame down.
    """
    from captchakraken.page_solver import PageSolver, PageSolverConfig

    cfg = PageSolverConfig(
        post_solve_outcome_timeout_ms=1,   # don't wait out the real poll
        post_solve_delay_ms=1,
        stale_element_backoff_ms=1,
    )
    solver = PageSolver(config=cfg)
    solver._last_mouse = (10.0, 20.0)

    state = {"detects": 0, "interacts": 0, "accepted": False}

    def detect(page):
        state["detects"] += 1
        return object()  # a truthy challenge handle

    def solve_single(page, element, retry_mode):
        state["interacts"] += 1
        if state["interacts"] == 1:
            return True, []          # submitted; verdict not in yet
        state["accepted"] = True     # vendor accepted, frame torn down
        raise RuntimeError(failure)

    def is_solved(page):
        if not state["accepted"]:
            return False
        if not solved_after_failure:
            raise RuntimeError("Target page, context or browser has been closed")
        return True

    solver.detect_captcha = detect
    solver._solve_single = solve_single
    solver.is_captcha_solved = is_solved
    return solver, state


class FakePage:
    pass


def test_solved_is_checked_before_spending_another_round():
    """The fix that matters: on a stale handle after interacting, ask the vendor
    whether the answer already landed BEFORE re-detecting.

    Before the fix this returned to the top of the loop, detected a third time
    and re-solved a puzzle that no longer existed — the race that produced
    TargetClosedError on the headed run.
    """
    solver, state = _solver("element is not attached to the DOM", solved_after_failure=True)

    result = solver.solve(FakePage())

    assert result.is_solved is True
    # `detect_captcha` is also called by the post-submit poll, so it is not the
    # signal. Interactions are: each one is a screenshot plus a vLLM round.
    assert state["interacts"] == 2, (
        f"the challenge pipeline ran {state['interacts']}x — expected 2. A third means "
        "the driver re-solved a puzzle that had already been accepted."
    )


def test_closed_page_raises_a_named_error_not_a_raw_playwright_message():
    """Whoever reads a gate failure should see 'the page closed', not
    'ElementHandle.screenshot failed' three retries later."""
    solver, state = _solver(
        "Page.query_selector: Target page, context or browser has been closed",
        solved_after_failure=False,
    )

    with pytest.raises(PageClosedError):
        solver.solve(FakePage())

    assert state["interacts"] == 2, (
        f"the challenge pipeline ran {state['interacts']}x — a closed target must not be "
        "retried, it can never answer"
    )
