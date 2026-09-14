import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken import planner
from captchakraken.page_solver import PageSolver, PageSolverConfig


class _Planner:
    def __init__(self):
        self.sampling = {}


def _solver(**kw):
    s = PageSolver(config=PageSolverConfig(**kw))
    s._solver.planner = _Planner()
    s._reset_animated_state()
    return s


def _answer(n):
    return [{"action": "click", "target_coordinates": [n, n]}]


def test_the_schedule_is_greedy_only():
    assert planner.RESAMPLE_TEMPERATURES == (0.0,), (
        "heating the re-ask scored 0/5 where greedy scored 4/5 and made failed "
        "attempts 10s slower. If this is being turned back on, it is because a "
        "REFUSAL signal now gates it — not because the model repeated itself.")


def test_no_level_ever_buys_a_temperature():
    for level in range(6):
        assert planner.sampling_for_level(level) == {}, (
            f"level {level} asked for a sampling override, and a hotter sample "
            f"is a worse answer on this model")


def test_the_first_look_at_a_board_is_greedy():
    s = _solver()
    s._note_answer(_answer(1), None)
    assert s._solver.planner.sampling == {}


def test_a_repeated_answer_still_counts_the_board_up():
    s = _solver()
    s._note_answer(_answer(1), None)
    s._note_answer(_answer(1), None)
    assert s._resample_level > 0
    assert s._solver.planner.sampling == {}, (
        "the level counted, and it must still buy nothing while the schedule "
        "is greedy — otherwise the two ports disagree about what a level means")


def test_a_fresh_board_goes_back_to_zero():
    s = _solver()
    s._note_answer(_answer(1), None)
    s._note_answer(_answer(1), None)
    assert s._resample_level > 0
    s._fresh_board()
    assert s._resample_level == 0
    assert s._solver.planner.sampling == {}


def test_a_new_solve_starts_at_zero():
    s = _solver()
    s._note_answer(_answer(1), None)
    s._note_answer(_answer(1), None)
    s._reset_animated_state()
    assert s._resample_level == 0
    assert s._solver.planner.sampling == {}


def test_the_level_still_reaches_the_planner_from_the_environment(monkeypatch):
    monkeypatch.setenv("CAPTCHA_RESAMPLE_LEVEL", "2")
    assert planner._sampling_from_env() == {}
