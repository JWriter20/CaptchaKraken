"""GeeTest's accepted state is neither a token nor an absence.

The verdict loop after an action knows three positive signals — an hCaptcha,
reCAPTCHA or Turnstile response token — and otherwise falls back on "the widget
is gone". GeeTest does neither when it accepts a drag: it paints a result banner
INSIDE the still-open panel and closes some seconds later. So the answer is
accepted, on screen, while every check the loop makes says no; the 1s window
expires; and the next round spends a WHOLE INFERENCE finding out the puzzle was
already solved.

MEASURED on gt4.geetest.com's slide demo, 2026-09-12, three consecutive live
solves — the driver opened another solve loop after the banner had painted,
every time:

    run 1   t+12873ms success  ->  "Captcha Solve Loop 3/6"
    run 2   t+25418ms success  ->  "Captcha Solve Loop 5/6"
    run 3   t+12571ms success  ->  "Captcha Solve Loop 3/6"

Over ten filmed attempts: 34 model calls for 20 drags, and ~6s between the
winning drag and the solve being reported.

The JS half is js/src/geetest-accept-is-a-signal.test.ts.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from captchakraken.page_solver import PageSolver, PageSolverConfig     # noqa: E402


class _El:
    def __init__(self, cls, visible):
        self.cls, self.visible = cls, visible


class _Page:
    """A page whose query_selector_all honours a comma-separated class selector."""

    def __init__(self, nodes):
        self.nodes = nodes

    def query_selector_all(self, sel):
        out = []
        for one in (s.strip() for s in sel.split(",")):
            want = [c for c in one.split(".") if c]
            for n in self.nodes:
                if all(c in n.cls.split() for c in want) and n not in out:
                    out.append(n)
        return out


def _solver():
    s = PageSolver(config=PageSolverConfig())
    s._visible = lambda el: bool(el is not None and el.visible)     # type: ignore
    return s


def test_the_accept_banner_inside_the_open_panel_is_a_solve():
    page = _Page([_El("geetest_result_tips geetest_success geetest_showResult", True)])
    assert _solver()._is_geetest_accepted(page) is True


def test_the_locked_anchor_after_the_panel_closes_is_a_solve():
    page = _Page([_El("geetest_captcha geetest_customTheme geetest_lock_success", True)])
    assert _solver()._is_geetest_accepted(page) is True


def test_a_refused_drag_is_not_a_solve():
    """The vendor's own discriminator: same element, `geetest_fail`. Reading it
    as a solve would report every miss as a pass."""
    page = _Page([
        _El("geetest_result_tips geetest_fail geetest_showResult", True),
        _El("geetest_captcha geetest_customTheme geetest_freeze_wait geetest_fail", True),
    ])
    assert _solver()._is_geetest_accepted(page) is False


def test_an_untouched_widget_is_not_a_solve():
    page = _Page([_El("geetest_result_tips", True),
                  _El("geetest_captcha geetest_customTheme", True)])
    assert _solver()._is_geetest_accepted(page) is False


def test_the_closed_popup_wrapper_does_not_count():
    """`geetest_popup_wrap` carries the success class at ZERO HEIGHT while the
    panel is shut, and sits before the real banner in document order."""
    page = _Page([_El(
        "geetest_popup_wrap geetest_popup geetest_customTheme geetest_lock_success", False)])
    assert _solver()._is_geetest_accepted(page) is False


def test_a_page_with_no_geetest_is_not_a_solve():
    assert _solver()._is_geetest_accepted(_Page([])) is False
