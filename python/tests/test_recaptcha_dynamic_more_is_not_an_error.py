from __future__ import annotations

import pytest

from captchakraken.page_solver import PageSolver


class _El:
    def __init__(self, text: str) -> None:
        self._text = text

    def text_content(self) -> str:
        return self._text

    def is_visible(self) -> bool:
        return True

    def bounding_box(self):
        return {"x": 0, "y": 0, "width": 200, "height": 20}


class _Frame:
    def __init__(self, selector: str | None, text: str) -> None:
        self._selector, self._text = selector, text

    def query_selector(self, selector: str):
        return _El(self._text) if selector == self._selector else None


class _BFrame:
    def __init__(self, frame: _Frame) -> None:
        self._frame = frame

    def content_frame(self):
        return self._frame


class _Page:
    def __init__(self, selector: str | None, text: str = "banner") -> None:
        self._frame = _Frame(selector, text)

    def query_selector(self, selector: str):
        if selector == 'iframe[src*="recaptcha/api2/bframe"]':
            return _BFrame(self._frame)
        return None


@pytest.fixture()
def solver() -> PageSolver:
    return PageSolver()


@pytest.mark.parametrize(
    "selector,text,expected",
    [
        (".rc-imageselect-incorrect-response", "Please try again.", "rejected"),
        (".rc-imageselect-error-select-more", "Please select all matching images.", "select-more"),
        (".rc-imageselect-error-dynamic-more", "Please also check the new images.", "dynamic-more"),
        (None, "", None),
    ],
)
def test_each_banner_is_named_separately(solver, selector, text, expected):
    assert solver._recaptcha_banner_kind(_Page(selector, text)) == expected


def test_dynamic_more_never_arms_the_abort():
    assert PageSolver._banner_is_fatal_after_retry("dynamic-more") is False


@pytest.mark.parametrize("kind", ["select-more", "rejected"])
def test_a_repeated_genuine_error_is_still_fatal(kind):
    assert PageSolver._banner_is_fatal_after_retry(kind) is True
