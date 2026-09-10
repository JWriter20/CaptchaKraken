"""Re-asking a refused board must not cost more than giving up did.

The obvious idea is to re-ask a board the vendor refused as a different SAMPLE:
the CLI sends `temperature: 0`, so greedy decoding on unchanged pixels with an
unchanged prompt returns the same answer by construction, and "the model
returned the same answer 3 times running" is therefore never evidence that the
model had nothing else to offer. Nothing had asked it for anything else.

IT WAS TRIED AND IT MEASURED WORSE. `RESAMPLE_TEMPERATURES = (0.0, 0.35, 0.7)`,
escalating one step each time a board came back with the answer it had already
given. Five puzzle types that a greedy driver passes: **0/5 against 4/5**, and
the median FAILED attempt cost **24.0s against 13.9s**. Two reasons, and they
compound:

  1. A hotter sample is a worse answer. The greedy read is the best single guess
     this model has, and the schedule spends rounds walking away from it.
  2. It defeats the give-up rule. `_no_progress_rounds` counts IDENTICAL
     answers, so making each re-ask differ resets the counter: the attempt stops
     bailing at round 3 and runs to the round ceiling instead.

So the schedule is greedy-only. The LEVEL plumbing stays, because the level is
the right thing to travel between the ports and a real re-ask needs it — but
the trigger has to be the vendor ACTUALLY refusing the answer, read off the
network response, rather than the model agreeing with itself.

These tests pin that: the level still counts and still travels, and it buys no
sampling override while the schedule says greedy.
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken import planner  # noqa: E402
from captchakraken.page_solver import PageSolver, PageSolverConfig  # noqa: E402


class _Planner:
    def __init__(self):
        self.sampling = {}


def _solver(**kw):
    s = PageSolver(config=PageSolverConfig(**kw))
    s._solver.planner = _Planner()
    s._reset_animated_state()
    return s


def _answer(n):
    """An answer whose signature depends only on `n`."""
    return [{"action": "click", "target_coordinates": [n, n]}]


def test_the_schedule_is_greedy_only():
    """The measurement above, as a gate.

    Re-enabling this is a product decision that has to be paid for with a new
    measurement, so it should break a test rather than merely change a tuple.
    """
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
    """The LEVEL is the plumbing worth keeping; only the schedule went away.

    A refusal signal read off the network is the trigger this wants, and it will
    need somewhere to record how many times this board has now been asked.
    """
    s = _solver()
    s._note_answer(_answer(1), None)
    s._note_answer(_answer(1), None)
    assert s._resample_level > 0
    assert s._solver.planner.sampling == {}, (
        "the level counted, and it must still buy nothing while the schedule "
        "is greedy — otherwise the two ports disagree about what a level means")


def test_a_fresh_board_goes_back_to_zero():
    """The count belongs to the BOARD, not the solve."""
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
    """The JS port is a fresh CLI process per round, so the level travels as
    `CAPTCHA_RESAMPLE_LEVEL`. That path must keep working, and must keep
    resolving to no override, or the two ports drift the moment a schedule
    comes back."""
    monkeypatch.setenv("CAPTCHA_RESAMPLE_LEVEL", "2")
    assert planner._sampling_from_env() == {}
