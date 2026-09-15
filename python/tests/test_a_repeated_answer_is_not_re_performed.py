import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken.page_solver import PageSolver, PageSolverConfig

ANSWER = [{"action": "click", "target_bounding_boxes": [[0.31, 0.226, 0.334, 0.25]]}]


def test_a_repeated_answer_is_reported_so_the_round_performs_nothing():
    solver = PageSolver(config=PageSolverConfig())
    solver._reset_animated_state()
    assert solver._note_answer(ANSWER, None) is False, "the first answer runs"
    assert solver._note_answer(ANSWER, None) is True, "the same answer again already ran and changed nothing"
    assert solver._no_progress_rounds == 1


def test_a_different_answer_resets_the_count():
    solver = PageSolver(config=PageSolverConfig())
    solver._reset_animated_state()
    solver._note_answer(ANSWER, None)
    solver._note_answer(ANSWER, None)
    other = [{"action": "click", "target_bounding_boxes": [[0.5, 0.5, 0.6, 0.6]]}]
    assert solver._note_answer(other, None) is False
    assert solver._no_progress_rounds == 0
