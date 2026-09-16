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


# A REFUSED ANSWER IS NO PROGRESS EVEN WHEN THE WORDS CHANGE. Once the film runs for the whole solve, every
# round re-slices a longer clip and the model answers differently, so the signature fence above never sees a
# repeat. Measured on the dispatched gate: video board windows went 41 to 67 for the same 102 pairs and the
# same ~240 attempts, which is rounds per board, not more work to do.
def test_a_refused_recording_counts_even_though_the_answer_changed():
    solver = PageSolver(config=PageSolverConfig())
    solver._reset_animated_state()
    asks = []

    for rnd in range(1, 7):
        if solver._refused_animated_asks >= solver.config.max_no_progress_rounds:
            break
        plan = solver._animated_plan
        if plan is not None and plan[2] is not None:
            solver._note_answer(plan[2], None)          # the reuse round: already refused, nothing is pressed
        else:
            answer = [{"action": "click", "target_bounding_boxes": [[0.1 * rnd, 0.1, 0.2, 0.2]]}]
            asks.append(rnd)
            solver._animated_plan = (["/tmp/k.png"], None, answer, [])
            solver._note_answer(answer, None)
    else:
        raise AssertionError("six rounds and the recording fence never tripped")

    assert solver._no_progress_rounds < solver.config.max_no_progress_rounds, (
        f"the signature fence reached {solver._no_progress_rounds}; this test is the case it cannot see")
    assert asks == [1, 3], f"the board was asked about on rounds {asks}, not twice"


def test_the_js_port_gives_up_on_a_refused_recording_too():
    js = (Path(__file__).resolve().parents[2] / "js" / "src" / "solver.ts").read_text()
    assert "this.refusedAnimatedAsks >= (cfg.maxNoProgressRounds ?? 2)" in js, (
        "the JS solve loop has no bound on refused recordings, so an animated board there runs every loop "
        "however hopeless it is while this port gives up")
