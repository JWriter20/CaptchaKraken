"""
Regression: GeeTest's submit control is a DIV that says "OK", and the verify
finder could not see it.

``_get_verify_button`` looked for two shapes only — a ``<button>`` whose text
contains one of Verify / Next / Submit / Skip, and a ``<div role="button">``
with the same texts — then two vendor fallbacks (``#recaptcha-verify-button``,
``.button-submit``). GeeTest matches none of them. Its control is

    <div class="geetest_submit geetest_disable">OK</div>

which is the wrong TAG (a bare div, no ``role``) and the wrong TEXT ("OK" is not
on the list). So it returned None and nothing was ever pressed.

WHAT THAT COST, AND WHY IT LOOKED LIKE THE MODEL

On GeeTest's ordered icon-click the model was right and the driver threw the
answer away. Measured live on 2026-08-19: the model returned three points that
landed on the three reference icons in order, and the cursor arrived within
0.005 normalised of each requested centre — and then no OK. The board does not
grade until you press it, so the solve loop re-read the same unchanged puzzle,
re-answered it identically, and gave up at the round cap. It scored 0/31 and
then 0/13, which reads exactly like a puzzle type the model cannot do. Every one
of those attempts was a correct answer.

That is why this pins the FINDER rather than a solve rate: a rate cannot tell
"answered wrongly" from "answered correctly and never sent".

The JS driver has the identical list in ``js/src/solver.ts::getVerifyButton``
and is pinned by ``js/src/geetest-submit-button.test.ts``. Per CLAUDE.md 1c the
two ports must behave the same.

Fakes rather than a browser: what is under test is which element the finder
SELECTS out of a known DOM. The fake resolves the selector forms the finder
actually uses, so it does not presuppose which one the fix reaches for.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from captchakraken.page_solver import PageSolver  # noqa: E402

ALPHABET = "abcdefghijklmnopqrstuvwxyz"


class FakeEl:
    def __init__(
        self,
        tag: str,
        classes: Optional[List[str]] = None,
        text: str = "",
        el_id: Optional[str] = None,
        role: Optional[str] = None,
        visible: bool = True,
    ) -> None:
        self.tag = tag
        self.classes = classes or []
        self.text = text
        self.el_id = el_id
        self.role = role
        self._visible = visible

    def is_visible(self) -> bool:
        return self._visible

    def __repr__(self) -> str:  # pragma: no cover - assertion output only
        return f"FakeEl({self.tag}, {self.classes}, {self.text!r})"


class FakeFrame:
    """Enough of Playwright's ``query_selector`` for the queries the finder makes."""

    def __init__(self, dom: List[FakeEl]) -> None:
        self.dom = dom

    def query_selector(self, selector: str) -> Optional[FakeEl]:
        if selector.startswith("xpath="):
            # Every quoted lowercase run is a candidate; the alphabet is
            # translate()'s own second argument, so drop it and keep the text.
            import re

            wanted = [w for w in re.findall(r"'([a-z]+)'", selector) if w != ALPHABET]
            if not wanted:
                return None
            needle = wanted[-1]
            for el in self.dom:
                shape_ok = el.tag == "button" or (el.tag == "div" and el.role == "button")
                if shape_ok and needle in el.text.lower():
                    return el
            return None
        if selector.startswith("#"):
            for el in self.dom:
                if el.el_id == selector[1:]:
                    return el
            return None
        classes = [c for c in selector.split(".") if c]
        for el in self.dom:
            if all(c in el.classes for c in classes):
                return el
        return None


# The GeeTest ordered-icon panel, as captured from gt4.geetest.com.
def geetest_panel() -> List[FakeEl]:
    return [
        FakeEl("div", ["geetest_box"], "Select in this order OK"),
        # The control. Note `geetest_disable`: GeeTest greys it until enough
        # icons are picked, and it is a plain div throughout.
        FakeEl("div", ["geetest_submit_14e1a298", "geetest_submit", "geetest_disable"], "OK"),
        # A decoy that also says "OK". A fix that matches on the word alone
        # would settle on the tooltip, which does nothing when pressed.
        FakeEl("div", ["geetest_submit_tips_14e1a298", "geetest_submit_tips"], "OK"),
    ]


def finder() -> PageSolver:
    return PageSolver.__new__(PageSolver)


def test_panel_really_does_defeat_the_old_two_shapes() -> None:
    """Guards the premise: if GeeTest ever ships a real Verify button, say so."""
    submit = next(el for el in geetest_panel() if "geetest_submit" in el.classes)
    assert submit.tag == "div"
    assert submit.role is None
    assert not any(t in submit.text.lower() for t in ("verify", "next", "submit", "skip"))


def test_finds_the_geetest_ok_control() -> None:
    found = finder()._get_verify_button(FakeFrame(geetest_panel()))
    assert found is not None, (
        "GeeTest's submit was not found, so a correctly answered icon puzzle is "
        "never sent — the board is re-read and re-answered until the round cap."
    )
    assert "geetest_submit" in found.classes, (
        f"found the wrong element ({found!r}); geetest_submit_tips is a tooltip"
    )


def test_vendor_fallbacks_still_win_where_they_should() -> None:
    solver = finder()

    recaptcha = solver._get_verify_button(
        FakeFrame([FakeEl("button", [], "", el_id="recaptcha-verify-button")])
    )
    assert recaptcha is not None and recaptcha.el_id == "recaptcha-verify-button"

    hcaptcha = solver._get_verify_button(FakeFrame([FakeEl("div", ["button-submit"], "")]))
    assert hcaptcha is not None and "button-submit" in hcaptcha.classes


def test_a_real_verify_button_is_still_preferred() -> None:
    found = finder()._get_verify_button(
        FakeFrame(
            [
                FakeEl("button", ["real-verify"], "Verify"),
                FakeEl("div", ["geetest_submit"], "OK"),
            ]
        )
    )
    assert found is not None and "real-verify" in found.classes, (
        "the named-text pass must keep first refusal; GeeTest is a fallback"
    )


def test_an_invisible_control_is_not_offered() -> None:
    found = finder()._get_verify_button(
        FakeFrame([FakeEl("div", ["geetest_submit"], "OK", visible=False)])
    )
    assert found is None, "a hidden submit must not be returned as pressable"
