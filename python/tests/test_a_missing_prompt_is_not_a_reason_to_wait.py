"""wait_for_selector('.prompt-text') throws when the element does not exist, and hCaptcha fixtures draw none: 12s and 18s timeouts per solve on the many-round types."""

import pytest

from captchakraken.page_solver import PageSolver, PageSolverConfig


class _Frame:

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
    frame = _Frame(has_prompt=False)
    _solver()._wait_for_hcaptcha_challenge_images(_Iframe(frame))
    assert frame.function_waits == 1, (
        "the gate never polled the imagery — it spent the whole timeout on a "
        "prompt element this board does not have, on every board")


def test_the_prompt_is_not_waited_for_as_a_separate_selector():
    frame = _Frame(has_prompt=True)
    _solver()._wait_for_hcaptcha_challenge_images(_Iframe(frame))
    assert ".prompt-text" not in frame.selector_waits, (
        "the prompt is being waited for by selector again; a board without one "
        "will pay the full timeout per round")


def test_a_board_with_a_prompt_is_still_gated_on_the_imagery():
    frame = _Frame(has_prompt=True)
    _solver()._wait_for_hcaptcha_challenge_images(_Iframe(frame))
    assert frame.function_waits == 1


def test_a_detached_frame_is_not_an_error():
    class _Gone:
        def content_frame(self):
            return None

    _solver()._wait_for_hcaptcha_challenge_images(_Gone())


def test_the_poll_itself_is_still_allowed_to_time_out():
    frame = _Frame(has_prompt=True)

    def boom(_expr, **kw):
        raise TimeoutError("still painting")

    frame.wait_for_function = boom
    _solver()._wait_for_hcaptcha_challenge_images(_Iframe(frame))
