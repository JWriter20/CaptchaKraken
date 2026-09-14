"""GeeTest paints success inside the open panel with no token, and `geetest_popup_wrap` carries the success class at zero height while shut, ahead of the real banner in document order. Before this check, 20 drags cost 34 model calls."""

from captchakraken.page_solver import PageSolver, PageSolverConfig
from fake_dom import FakeNode, fake_scope


def _solved(nodes) -> bool:
    return PageSolver(config=PageSolverConfig()).is_captcha_solved(fake_scope(nodes))


def test_the_accept_banner_inside_the_open_panel_is_a_solve():
    assert _solved([FakeNode([".geetest_result_tips.geetest_success"])]) is True


def test_the_locked_anchor_after_the_panel_closes_is_a_solve():
    assert _solved([FakeNode([".geetest_captcha.geetest_lock_success"])]) is True


def test_a_refused_drag_is_not_a_solve():
    assert _solved([FakeNode([".geetest_result_tips.geetest_fail"]), FakeNode([".geetest_captcha.geetest_fail"])]) is False


def test_an_untouched_widget_is_not_a_solve():
    assert _solved([FakeNode([".geetest_result_tips"]), FakeNode([".geetest_captcha"])]) is False


def test_the_closed_popup_wrapper_does_not_count():
    assert _solved([FakeNode([".geetest_captcha.geetest_lock_success"], visible=False)]) is False


def test_a_page_with_no_geetest_is_not_a_solve():
    assert _solved([]) is False
