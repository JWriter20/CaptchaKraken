"""GeeTest's control is `<div class="geetest_submit geetest_disable">OK</div>` (wrong tag, wrong word) beside decoy tooltips that also say OK. It scored 0/31 and then 0/13, which reads exactly like a puzzle the model cannot do. Pin the finder, not a rate."""

from __future__ import annotations

from captchakraken.page_solver import PageSolver
from captchakraken.selectors import submit_by_text
from fake_dom import FakeNode, fake_scope


def geetest_panel():
    return [
        FakeNode([".geetest_box"], text="Select in this order OK"),
        FakeNode([".geetest_submit"], text="OK"),
        FakeNode([".geetest_submit_tips"], text="OK"),
    ]


def _find(nodes):
    found = PageSolver.__new__(PageSolver)._get_verify_button(fake_scope(nodes))
    return found.node if found is not None else None


def test_finds_the_geetest_ok_control() -> None:
    found = _find(geetest_panel())
    assert found is not None, (
        "GeeTest's submit was not found, so a correctly answered icon puzzle is "
        "never sent — the board is re-read and re-answered until the round cap."
    )
    assert ".geetest_submit" in found.matches, f"found the wrong element ({found!r}); geetest_submit_tips is a tooltip"


def test_vendor_fallbacks_still_win_where_they_should() -> None:
    assert "#recaptcha-verify-button" in _find([FakeNode(["#recaptcha-verify-button"])]).matches
    assert ".button-submit" in _find([FakeNode([".button-submit"])]).matches


def test_a_real_verify_button_is_still_preferred() -> None:
    found = _find([FakeNode([".geetest_submit"], text="OK"), FakeNode([submit_by_text("verify")], text="Verify")])
    assert submit_by_text("verify") in found.matches, "the named-text pass must keep first refusal; GeeTest is a fallback"


def test_an_invisible_control_is_not_offered() -> None:
    assert _find([FakeNode([".geetest_submit"], text="OK", visible=False)]) is None, "a hidden submit must not be returned as pressable"
