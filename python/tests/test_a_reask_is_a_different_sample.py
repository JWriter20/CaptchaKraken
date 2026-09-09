"""Re-asking a refused board must not re-run the same arithmetic.

`planner` sends `temperature: 0`. Greedy decoding on unchanged pixels with an
unchanged prompt returns the SAME answer, every time, by construction — so the
driver's "no progress: the model returned the same answer 3 times running" was
never evidence that the model had nothing else to offer. Nothing had asked it
for anything else. Three rounds, three identical requests, and then a
`CaptchaSolveError` blaming the model.

Measured on the 9 Sep Tier 3: that message is the reason for essentially every
non-solve, and a failed attempt costs ~20s of budget reaching it.

So a board the vendor has REFUSED is re-asked as a different sample. The first
look stays greedy — it is the best single guess and every solve that works today
works on it, so the schedule must cost the common case nothing.
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

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


def test_the_first_look_at_a_board_is_greedy():
    s = _solver()
    s._note_answer(_answer(1), None)
    assert s._solver.planner.sampling == {}, (
        "a first read must stay deterministic — every solve that works today "
        "works on the greedy answer")


def test_the_same_answer_coming_back_buys_a_different_sample():
    s = _solver()
    s._note_answer(_answer(1), None)
    s._note_answer(_answer(1), None)          # the vendor refused it; same again
    got = s._solver.planner.sampling
    assert got.get("temperature", 0) > 0, (
        "the board was re-read and answered identically, and the next request "
        "is still greedy — so it will answer identically again")
    assert "seed" in got, "two re-asks at one temperature must be two samples"


def test_it_escalates_rather_than_repeating_one_temperature():
    s = _solver()
    s._note_answer(_answer(1), None)
    temps = []
    for _ in range(3):
        s._note_answer(_answer(1), None)
        temps.append(s._solver.planner.sampling.get("temperature"))
    assert temps == sorted(temps), f"temperature must not go backwards: {temps}"
    assert temps[-1] > temps[0], (
        "a board that has refused several readings wants a different reading, "
        "not the same nudge again")


def test_it_stops_at_the_top_of_the_schedule():
    """Unbounded heat is not a strategy — past the last entry it holds."""
    from captchakraken import planner
    s = _solver()
    s._note_answer(_answer(1), None)
    for _ in range(12):
        s._note_answer(_answer(1), None)
    assert (s._solver.planner.sampling.get("temperature")
            == planner.RESAMPLE_TEMPERATURES[-1])


def test_a_fresh_board_goes_back_to_greedy():
    """The escalation belongs to the BOARD, not the solve.

    A new puzzle has never been read, so the greedy answer has not been tested
    against it — carrying the temperature over would spend variance on a
    question nobody has asked yet.
    """
    s = _solver()
    s._note_answer(_answer(1), None)
    s._note_answer(_answer(1), None)
    assert s._solver.planner.sampling.get("temperature", 0) > 0
    s._fresh_board()
    assert s._solver.planner.sampling == {}


def test_a_new_solve_starts_greedy():
    s = _solver()
    s._note_answer(_answer(1), None)
    s._note_answer(_answer(1), None)
    assert s._solver.planner.sampling != {}
    s._reset_animated_state()
    assert s._solver.planner.sampling == {}
    assert s._resample_level == 0


def test_a_different_answer_keeps_the_heat():
    """The counter is per BOARD, not per repeat.

    `_no_progress_rounds` resets whenever an answer differs, and dropping the
    temperature with it would walk straight back to the greedy answer the
    vendor already refused — an oscillation, not a search.
    """
    s = _solver()
    s._note_answer(_answer(1), None)
    s._note_answer(_answer(1), None)
    hot = s._solver.planner.sampling.get("temperature")
    s._note_answer(_answer(2), None)          # a different answer, still wrong
    assert s._solver.planner.sampling.get("temperature") == hot, (
        "the board is unsolved and unchanged; going back to greedy would "
        "re-emit the answer that was already refused")
