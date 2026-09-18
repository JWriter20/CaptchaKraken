# Which expert a board is asked about is decided before the board has necessarily painted.
#
# `text_mode` is "is there a text box in this widget", and it picks the expert for the round. The check
# runs at the top of the round, and a vendor frame can be resolved while still holding nothing at all —
# measured under camoufox on 2026-09-17: yandex_text and mtcaptcha_text had `frame=yes` and a frame with
# ZERO elements, so the box was missed and a distorted-text board was sent to the still expert. Chromium
# painted faster and never showed it.
#
# The wait is paid only when the frame is empty: a board that has painted anything, text or not, is
# answered immediately.
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken.page_solver import PageSolver, PageSolverConfig  # noqa: E402


class _Locator:
    def __init__(self, frame, selector):
        self._frame, self._selector = frame, selector

    def count(self):
        self._frame.counted += 1
        return len(self._frame.matches(self._selector))

    @property
    def first(self):
        return self


class _Frame:
    """A frame that paints after `paints_after` reads, like one still loading."""

    def __init__(self, paints_after=0, has_input=True):
        self.paints_after, self.has_input, self.counted = paints_after, has_input, 0

    def matches(self, selector):
        if self.counted < self.paints_after:
            return []
        if selector == "body *":
            return ["something"]
        return ["input"] if self.has_input and "input" in selector else []

    def locator(self, selector):
        return _Locator(self, selector)


def _solver():
    solver = PageSolver(config=PageSolverConfig())
    # The finder itself is exercised elsewhere; here the question is only whether it is asked twice.
    solver._find_control = lambda scope, sels: ("box" if scope.matches("input[type=text]") else None)
    return solver


def test_a_frame_that_has_not_painted_is_given_a_moment():
    solver = _solver()
    frame = _Frame(paints_after=2, has_input=True)
    assert solver._answer_box(frame) == "box", "the round was routed before the frame had anything in it"


def test_a_painted_board_with_no_text_box_is_not_waited_on():
    """Every slide and click vendor goes through here; they must not pay for the fix."""
    solver = _solver()
    frame = _Frame(paints_after=0, has_input=False)
    assert solver._answer_box(frame) is None
    assert frame.counted <= 2, f"waited on a board that had already painted ({frame.counted} reads)"


def test_a_box_that_is_there_immediately_costs_nothing():
    solver = _solver()
    frame = _Frame(paints_after=0, has_input=True)
    assert solver._answer_box(frame) == "box"
    assert frame.counted <= 1


def test_a_frame_that_never_paints_gives_up():
    solver = _solver()
    solver._EMPTY_FRAME_TIMEOUT_MS = 120
    frame = _Frame(paints_after=10_000, has_input=True)
    assert solver._answer_box(frame) is None
