"""GeeTest paints success inside the open panel with no token, and `geetest_popup_wrap` carries the success class at zero height while shut, ahead of the real banner in document order. Before this check, 20 drags cost 34 model calls."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from captchakraken.page_solver import PageSolver, PageSolverConfig


class _El:
    def __init__(self, cls, visible):
        self.cls, self.visible = cls, visible


class _Page:

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
    s._visible = lambda el: bool(el is not None and el.visible)
    return s


def test_the_accept_banner_inside_the_open_panel_is_a_solve():
    page = _Page([_El("geetest_result_tips geetest_success geetest_showResult", True)])
    assert _solver()._is_geetest_accepted(page) is True


def test_the_locked_anchor_after_the_panel_closes_is_a_solve():
    page = _Page([_El("geetest_captcha geetest_customTheme geetest_lock_success", True)])
    assert _solver()._is_geetest_accepted(page) is True


def test_a_refused_drag_is_not_a_solve():
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
    page = _Page([_El(
        "geetest_popup_wrap geetest_popup geetest_customTheme geetest_lock_success", False)])
    assert _solver()._is_geetest_accepted(page) is False


def test_a_page_with_no_geetest_is_not_a_solve():
    assert _solver()._is_geetest_accepted(_Page([])) is False
