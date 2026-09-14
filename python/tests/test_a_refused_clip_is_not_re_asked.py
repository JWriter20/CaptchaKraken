import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken.page_solver import PageSolver, PageSolverConfig


def _armed(tmp_path, steady_screens):
    solver = PageSolver(config=PageSolverConfig())
    solver._reset_animated_state()
    kfdir = tmp_path / f"clip_{steady_screens}"
    kfdir.mkdir()
    frames = [str(kfdir / "frame_01.png")]
    for f in frames:
        Path(f).write_bytes(b"not-really-a-png")
    solver._animated_plan = (frames, str(kfdir), ["an answer"], [{"usage": 1}])
    solver._keyframe_steady_screens = steady_screens
    solver._keyframe_mode = "even" if steady_screens == 0 else "steady"
    return solver, kfdir


@pytest.mark.parametrize("steady", [2, 3, 6])
def test_a_cycling_clip_keeps_its_frames(tmp_path, steady):
    solver, kfdir = _armed(tmp_path, steady)
    solver._invalidate_animated_answer()

    plan = solver._animated_plan
    assert plan is not None, "a cycling clip must survive a refusal"
    assert plan[2] is None, "the refused ANSWER must be dropped"
    assert kfdir.exists(), "the frames must still be on disk to be re-asked"


@pytest.mark.parametrize("steady", [0, 1])
def test_a_clip_with_no_steady_screen_is_thrown_away(tmp_path, steady):
    solver, kfdir = _armed(tmp_path, steady)
    solver._invalidate_animated_answer()

    assert solver._animated_plan is None, (
        "the recording survived a refusal it cannot answer differently — the "
        "next round will re-ask the same slices and get the same answer")
    assert not kfdir.exists(), "the frames should have been deleted with the plan"
    assert solver._keyframe_steady_screens == 0
    assert solver._keyframe_mode is None, (
        "the classification belongs to a recording that no longer exists")


def test_a_plan_that_was_already_invalidated_is_left_alone(tmp_path):
    solver, kfdir = _armed(tmp_path, 3)
    solver._invalidate_animated_answer()
    solver._invalidate_animated_answer()
    assert solver._animated_plan is not None
    assert kfdir.exists()


def test_no_plan_is_not_an_error():
    solver = PageSolver(config=PageSolverConfig())
    solver._reset_animated_state()
    solver._invalidate_animated_answer()
    assert solver._animated_plan is None
