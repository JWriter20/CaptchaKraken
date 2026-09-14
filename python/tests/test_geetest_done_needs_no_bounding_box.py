from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from captchakraken.page_solver import answer_needs_element_box


def test_a_done_only_answer_needs_no_box():
    assert answer_needs_element_box([{"action": "done"}]) is False
    assert answer_needs_element_box([{"action": "done"}, {"action": "done"}]) is False


def test_every_coordinate_action_still_needs_one():
    for action in ("click", "drag", "type", "slide", "move"):
        assert answer_needs_element_box([{"action": action}]) is True, action


def test_a_done_mixed_with_real_work_still_needs_one():
    assert answer_needs_element_box([{"action": "done"}, {"action": "click"}]) is True
    assert answer_needs_element_box([{"action": "click"}, {"action": "done"}]) is True


def test_an_unrecognised_action_defaults_to_needing_one():
    assert answer_needs_element_box([{"action": "some_future_gesture"}]) is True
    assert answer_needs_element_box([{}]) is True


def test_an_empty_answer_needs_nothing():
    assert answer_needs_element_box([]) is False
