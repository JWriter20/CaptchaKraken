"""`find_checkbox` decides where the driver clicks first, before any model runs.

Every reCAPTCHA solve starts by clicking "I'm not a robot". A false positive
here sends the pointer somewhere else on the page and the solve never begins; a
false negative reports no checkbox on a page that plainly has one. Neither
raises, so both look like the model failing.

The detector is pure OpenCV with five gates — squareness, absolute size, area
relative to the image, extent, and content variance. Each test below is one of
those gates, driven with a synthetic image so it runs anywhere with no captures.
"""

import cv2
import numpy as np
import pytest

from captchakraken.tool_calls.find_checkbox import find_checkbox


def _canvas(path, size=(400, 300)):
    """A white page. The detector thresholds at 200, so white is background."""
    img = np.full((size[1], size[0], 3), 255, dtype=np.uint8)
    return img


def _save(path, img):
    cv2.imwrite(str(path), img)
    return str(path)


def _draw_square(img, x, y, side, thickness=3, colour=(0, 0, 0)):
    cv2.rectangle(img, (x, y), (x + side, y + side), colour, thickness)
    return img


def test_a_plain_square_outline_is_found(tmp_path):
    img = _draw_square(_canvas(tmp_path), 120, 90, 40)
    box = find_checkbox(_save(tmp_path / "cb.png", img))

    assert box is not None, "a 40px square outline on a white page was not detected"
    x, y, w, h = box
    assert abs(x - 120) <= 6 and abs(y - 90) <= 6, f"box landed at {(x, y)}, not near (120, 90)"
    assert abs(w - h) <= 4, "a checkbox must come back roughly square"


def test_a_blank_page_reports_nothing_rather_than_guessing(tmp_path):
    """No widget is a real answer. Returning a box anyway would click the page
    background and start a solve against nothing."""
    assert find_checkbox(_save(tmp_path / "blank.png", _canvas(tmp_path))) is None


def test_an_unreadable_path_returns_none_instead_of_raising(tmp_path):
    """Callers treat None as "no checkbox here" and move on. Raising instead
    would turn a missing screenshot into a solve-level crash."""
    assert find_checkbox(str(tmp_path / "does-not-exist.png")) is None


def test_a_wide_rectangle_is_not_a_checkbox(tmp_path):
    """The squareness gate. A form field, a button, or a banner is a rectangle
    of the right size and the wrong shape."""
    img = _canvas(tmp_path)
    cv2.rectangle(img, (100, 100), (280, 145), (0, 0, 0), 3)  # 180x45, aspect 4:1
    assert find_checkbox(_save(tmp_path / "wide.png", img)) is None


def test_a_tiny_square_is_not_a_checkbox(tmp_path):
    """The absolute-size gate, which is what keeps punctuation and icon noise
    out. Below 20px a glyph is indistinguishable from a box."""
    img = _draw_square(_canvas(tmp_path), 150, 120, 12, thickness=2)
    assert find_checkbox(_save(tmp_path / "tiny.png", img)) is None


def test_a_square_full_of_detail_is_not_a_checkbox(tmp_path):
    """The content gate. A photo tile is square, the right size, and solid at the
    edges — the only thing that separates it from a checkbox is that a checkbox
    is empty inside. Without this gate the first tile of a 3x3 grid gets clicked
    as if it were the widget."""
    img = _canvas(tmp_path)
    x, y, side = 140, 110, 44
    _draw_square(img, x, y, side)
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 255, size=(side - 10, side - 10, 3), dtype=np.uint8)
    img[y + 5:y + side - 5, x + 5:x + side - 5] = noise

    assert find_checkbox(_save(tmp_path / "busy.png", img)) is None


def test_a_square_larger_than_a_widget_is_not_a_checkbox(tmp_path):
    """The relative-area gate: above 5% of the image it is a panel, a card or the
    challenge frame itself, not the checkbox inside one."""
    img = _draw_square(_canvas(tmp_path), 20, 20, 200)  # 40000px of a 120000px image
    assert find_checkbox(_save(tmp_path / "huge.png", img)) is None
