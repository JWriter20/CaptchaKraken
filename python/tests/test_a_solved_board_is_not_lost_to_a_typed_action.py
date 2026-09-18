# A solve the vendor accepted must not be reported as a failure because the answer was typed.
#
# `answer_needs_element_box` called `.get` on each action. Every other reader in the solver goes through
# `_as_dict` first, because the planner returns TYPED actions (`ClickAction`, `TypeAction`, `DoneAction`)
# as readily as dicts — so this one raised `AttributeError: 'ClickAction' object has no attribute 'get'`.
#
# It only runs when the element has no bounding box, and that is what a widget which is CLOSING looks
# like — which is to say, the widget that has just accepted the answer. Measured on 2026-09-17 against
# the hosted endpoint: mtcaptcha_text, prosopo_grid_3x3 and yidun_iconclick each graded `solved: true`
# on the board and then ended the attempt with that AttributeError. Tier 3 files it as a lost solve:
# "an answer the board would have accepted, and the solve was reported as failed".
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken.action_types import ClickAction, DoneAction, TypeAction  # noqa: E402
from captchakraken.page_solver import answer_needs_element_box  # noqa: E402


@pytest.mark.parametrize("action, needs_box", [
    (ClickAction(action="click", target_bounding_boxes=[[0.1, 0.1, 0.2, 0.2]]), True),
    (TypeAction(action="type", text="abc"), True),
    (DoneAction(action="done"), False),
])
def test_a_typed_answer_is_read_like_any_other(action, needs_box):
    assert answer_needs_element_box([action]) is needs_box


@pytest.mark.parametrize("action, needs_box", [
    ({"action": "click", "target_bounding_boxes": [[0.1, 0.1, 0.2, 0.2]]}, True),
    ({"action": "done"}, False),
])
def test_a_dict_answer_still_reads_the_same(action, needs_box):
    assert answer_needs_element_box([action]) is needs_box


def test_nothing_to_do_needs_nothing():
    assert answer_needs_element_box([]) is False
    assert answer_needs_element_box([None]) is False


def test_a_done_beside_a_click_still_needs_the_box():
    """The check is "does ANY action need geometry", and a mixed answer does."""
    mixed = [DoneAction(action="done"),
             ClickAction(action="click", target_bounding_boxes=[[0.4, 0.4, 0.6, 0.6]])]
    assert answer_needs_element_box(mixed) is True
