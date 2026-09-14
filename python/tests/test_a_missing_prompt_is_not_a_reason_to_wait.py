"""A readiness gate must not block on an element that is not there.

`_wait_for_hcaptcha_challenge_images` waits for a board to finish painting
before an inference is spent on it. It used to open with its own
`wait_for_selector(".prompt-text", state="visible")`, and that call THROWS when
the element does not exist — so a challenge with no `.prompt-text` paid the
entire `hcaptcha_images_timeout_ms` and then, because the whole body is
best-effort, carried on as though nothing had happened. Silent, and per BOARD.

Measured 2026-09-13 over the Tier 3 hCaptcha fixtures, none of which draw a
`.prompt-text` (the instruction is in the rendered pixels, as it is on several
real hCaptcha variants):

    grocery_list          1 board    hcaptcha-images  3.0s   solved
    click_blocked_lines   5 boards   hcaptcha-images 12.0s   TIMED OUT
    tower_stack           7 boards   hcaptcha-images 18.0s   TIMED OUT

The cost lands on whichever puzzle takes the most rounds — the ones with the
least budget left to lose. Both of those types solved 2/2 before the client
began recognising these boards as hCaptcha at all, and 0/2 after.

The function's own docstring had already reasoned this out for the example
image: "a gate can only report on what it can see; with nothing to check it has
no opinion, and no opinion must not read as not ready". The prompt was the one
place that rule had not been applied.

Mirror of the same case in board-paint-gate.test.ts — CLAUDE.md 1c.
"""

import pytest

from captchakraken.page_solver import PageSolver, PageSolverConfig


class _Frame:
    """Records what the gate asked, and refuses `.prompt-text` the way a real
    frame without one does — by raising when the wait expires."""

    def __init__(self, has_prompt: bool):
        self.has_prompt = has_prompt
        self.selector_waits = []
        self.function_waits = 0

    def wait_for_selector(self, selector, **kw):
        self.selector_waits.append(selector)
        if selector == ".prompt-text" and not self.has_prompt:
            raise TimeoutError(f"Timeout waiting for selector {selector!r}")

    def wait_for_function(self, _expr, **kw):
        self.function_waits += 1


class _Iframe:
    def __init__(self, frame):
        self._frame = frame

    def content_frame(self):
        return self._frame


def _solver():
    return PageSolver(config=PageSolverConfig())


def test_a_board_without_a_prompt_still_reaches_the_image_check():
    """THE REGRESSION. The prompt wait threw, the except swallowed it, and the
    poll that actually looks at the imagery never ran at all."""
    frame = _Frame(has_prompt=False)
    _solver()._wait_for_hcaptcha_challenge_images(_Iframe(frame))
    assert frame.function_waits == 1, (
        "the gate never polled the imagery — it spent the whole timeout on a "
        "prompt element this board does not have, on every board")


def test_the_prompt_is_not_waited_for_as_a_separate_selector():
    """Folded into the poll instead, so 'absent' and 'still painting' stop
    being the same answer."""
    frame = _Frame(has_prompt=True)
    _solver()._wait_for_hcaptcha_challenge_images(_Iframe(frame))
    assert ".prompt-text" not in frame.selector_waits, (
        "the prompt is being waited for by selector again; a board without one "
        "will pay the full timeout per round")


def test_a_board_with_a_prompt_is_still_gated_on_the_imagery():
    """The gate did not become a no-op: the poll still runs."""
    frame = _Frame(has_prompt=True)
    _solver()._wait_for_hcaptcha_challenge_images(_Iframe(frame))
    assert frame.function_waits == 1


def test_a_detached_frame_is_not_an_error():
    """Best-effort, unchanged: no content frame means nothing to wait for."""
    class _Gone:
        def content_frame(self):
            return None

    _solver()._wait_for_hcaptcha_challenge_images(_Gone())   # must not raise


def test_the_poll_itself_is_still_allowed_to_time_out():
    """A board that never finishes painting falls through to the screenshot
    rather than failing the solve — the fail-fast path downstream covers a
    genuinely unsupported puzzle."""
    frame = _Frame(has_prompt=True)

    def boom(_expr, **kw):
        raise TimeoutError("still painting")

    frame.wait_for_function = boom
    _solver()._wait_for_hcaptcha_challenge_images(_Iframe(frame))   # must not raise
