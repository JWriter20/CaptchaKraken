"""Re-asking the same pictures cannot produce a different answer.

`_invalidate_animated_answer` exists because the driver used to re-SUBMIT a
refused animated answer without ever asking the model again. It drops the answer
and keeps the frames, which is right for a CYCLING board: the frames do not
change, the answer merely landed on the wrong screen, and re-asking them is the
whole point.

It is wrong for a clip the slicer could not find a steady screen in.
`steady_screens` is how many distinct screens the slicer could PROVE the clip
comes back to, and zero means a continuous animation — the six keyframes are
arbitrary slices of something that never holds still, the frame number in the
answer refers to nothing, and asking the same question about the same pictures
returns the same answer. The solve then dies on `max_no_progress_rounds` having
learned nothing. Resampling does not rescue it: a temperature moves the
coordinate a little, not the reading.

MEASURED 2026-09-13 on an hCaptcha growing-item animation, the last type that passed on the
JS port and failed on python:

    the recording   41 frames, 39 distinct screens, mode=even, steady_screens=0
    the gold        frame: null, free — the frame carries no information at all
    python          4 rounds: (0.738, 0.657), (0.738, 0.657), (0.725, 0.657),
                    (0.725, 0.657) — every one scored 0.0, then "no progress"
    js              round 2 re-read the board as a STILL: (0.553, 0.425), 1.0

Dropping the frames is what lets the next round re-classify, so a board that has
stopped moving is read as the still it now is. That is the JS behaviour, and
CLAUDE.md 1c says the two ports must not differ on it.
"""
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken.page_solver import PageSolver, PageSolverConfig  # noqa: E402


def _armed(tmp_path, steady_screens):
    """A solver holding a recorded plan whose clip has `steady_screens`."""
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
    """The case the function was written for. The board really does come back to
    these screens, so the frames are the truth and only the answer was wrong —
    re-recording would cost another burst and buy nothing."""
    solver, kfdir = _armed(tmp_path, steady)
    solver._invalidate_animated_answer()

    plan = solver._animated_plan
    assert plan is not None, "a cycling clip must survive a refusal"
    assert plan[2] is None, "the refused ANSWER must be dropped"
    assert kfdir.exists(), "the frames must still be on disk to be re-asked"


@pytest.mark.parametrize("steady", [0, 1])
def test_a_clip_with_no_steady_screen_is_thrown_away(tmp_path, steady):
    """A continuous animation, and a clip with a single screen: neither gives the
    frame number anything to refer to, so re-asking it is arithmetic."""
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
    """Called once per repeated answer, so it must be idempotent — and must not
    delete the frames of a cycling clip on the second call."""
    solver, kfdir = _armed(tmp_path, 3)
    solver._invalidate_animated_answer()
    solver._invalidate_animated_answer()
    assert solver._animated_plan is not None
    assert kfdir.exists()


def test_no_plan_is_not_an_error():
    solver = PageSolver(config=PageSolverConfig())
    solver._reset_animated_state()
    solver._invalidate_animated_answer()      # must not raise
    assert solver._animated_plan is None
