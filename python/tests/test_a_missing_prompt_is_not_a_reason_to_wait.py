"""wait_for_selector('.prompt-text') throws when the element does not exist, and hCaptcha fixtures draw none: 12s and 18s timeouts per solve on the many-round types."""

from captchakraken.kinds import Vendor
from captchakraken.page_solver import PageSolver, PageSolverConfig
from captchakraken.selectors import SELECTORS


class _Frame:
    def __init__(self):
        self.selector_waits = []
        self.function_waits = []

    def wait_for_selector(self, selector, **kw):
        self.selector_waits.append(selector)
        raise TimeoutError(f"Timeout waiting for selector {selector!r}")

    def wait_for_function(self, _expr, arg=None, **kw):
        self.function_waits.append(arg)


def _wait(frame):
    PageSolver(config=PageSolverConfig())._wait_for_board_images(frame, SELECTORS[Vendor.HCAPTCHA])


def test_the_prompt_is_not_waited_for_as_a_separate_selector():
    frame = _Frame()
    _wait(frame)
    assert frame.selector_waits == [], (
        "the prompt is being waited for by selector again; a board without one "
        "will pay the full timeout per round")


def test_the_board_is_gated_on_the_imagery_in_one_poll():
    frame = _Frame()
    _wait(frame)
    assert len(frame.function_waits) == 1
    assert frame.function_waits[0] == {"prompt": ".prompt-text", "images": ",".join(SELECTORS[Vendor.HCAPTCHA].images)}


def test_the_poll_itself_is_still_allowed_to_time_out():
    frame = _Frame()

    def boom(_expr, **kw):
        raise TimeoutError("still painting")

    frame.wait_for_function = boom
    _wait(frame)
