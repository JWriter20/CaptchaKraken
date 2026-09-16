# One burst can only show the model the screens that fell inside its window — measured, a 4s burst against a 5.3s cycle showed
# two screens of three — and the answer it produces is identical next round by construction, because sampling is greedy over the
# same frames. So a refused animated answer was re-pressed until the no-progress fence tripped, three rounds in, with half the
# solve budget unspent. Measured live 2026-09-15: hCaptcha's animated board read 2/15 and 6/12 on the two served models.
#
# The film now accumulates for the whole solve, so every round asks against strictly more of the board than the last ask saw.
# The JS port films CONTINUOUSLY, through inference and the verdict wait; sync Playwright is thread-affine, so this port films
# another chunk per round instead. Different density, same contract — which is what these pin.
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken import page_solver
from captchakraken.page_solver import PageSolver, PageSolverConfig
from virtual_clock import install

_SCREENS = [b"screen-A" + b"\x00" * 64, b"screen-B" + b"\x11" * 64, b"screen-C" + b"\x22" * 64]


def _solver(monkeypatch):
    """A solver filming a 3-screen cycle, with the slicer stubbed: this is about WHAT is sliced, not how."""
    import cv2
    import numpy as np

    install(monkeypatch, page_solver)
    solver = PageSolver(config=PageSolverConfig())
    solver._reset_animated_state()
    solver._deadline_ms = None
    sliced: list = []
    shots = {"n": 0}

    def fake_shot(element, path, animations="allow", timeout_ms=None):
        with open(path, "wb") as fh:
            fh.write(_SCREENS[shots["n"] % len(_SCREENS)])
        shots["n"] += 1

    def fake_slice(frames, burst_ms):
        sliced.append(len(frames))
        return ["/tmp/ck_fake_keyframe.png"], "/tmp/ck_fake_dir"

    monkeypatch.setattr(solver, "_screenshot", fake_shot)
    monkeypatch.setattr(solver, "_slice", fake_slice)
    monkeypatch.setattr(cv2, "imread", lambda p: np.zeros((4, 4, 3), dtype=np.uint8))
    return solver, sliced


def test_a_second_round_slices_everything_filmed_so_far(monkeypatch):
    solver, sliced = _solver(monkeypatch)
    solver._record_keyframes(object())
    first_film = len(solver._film_frames)
    solver._record_keyframes(object())

    assert len(sliced) == 2
    # Against the accumulated film, not against the first slice: the two bursts do not film the same number
    # of frames (the second starts at a different phase of the cycle, so it closes at a different point), and
    # `sliced[1] > sliced[0]` is true by accident even when the first film is thrown away.
    assert sliced[1] == len(solver._film_frames), (
        f"the second round sliced {sliced[1]} frames while the film holds {len(solver._film_frames)} — it "
        f"sliced only its own burst, so the re-ask is the same question and the answer will be the same")
    assert sliced[0] == first_film
    assert len(solver._film_frames) > first_film, "the second round filmed nothing new"


def test_the_film_dies_with_the_board_not_with_the_answer(monkeypatch):
    solver, _ = _solver(monkeypatch)
    solver._record_keyframes(object())
    filmed = len(solver._film_frames)
    assert filmed, "nothing was filmed"

    # A refused answer: the answer is spent, the film is the only thing that can improve the next question.
    solver._discard_animated_plan()
    assert len(solver._film_frames) == filmed, "a refused answer threw away the film that outlives it"

    # A board that is gone: its film can never describe the next one.
    solver._stop_animated_film()
    assert solver._film_frames == [], "the film of a board that is gone was kept"
    assert solver._film_ms == 0.0


def test_a_new_solve_films_a_new_board(monkeypatch):
    solver, _ = _solver(monkeypatch)
    solver._record_keyframes(object())
    assert solver._film_frames, "nothing was filmed"
    solver._reset_animated_state()
    assert solver._film_frames == [], (
        "a film leaking into the next captcha would answer it with pictures of the previous one")


def test_the_js_port_also_keeps_filming():
    js = (Path(__file__).resolve().parents[2] / "js" / "src" / "solver.ts").read_text()
    assert "private animatedFilm" in js, (
        "the JS port has no camera outliving a round, so a refused animated answer there is still re-pressed")
    assert "this.discardAnimatedPlan();" in js.split("if (this.noteAnswer(")[1][:600], (
        "the JS port keeps a refused animated plan, which hands the no-progress fence the same signature "
        "three rounds running while this port re-asks")
