"""Filming while the model is being asked is only free if the camera stops first.

`_speculate` reads the still and films the widget at once, and the claim that
makes that free is that "the recording happens inside a wait the solve was
making anyway". It was not free. The burst's settled exit was
`if fut.done() and settled: break` — so once a board had shown one screen and
held it for a full floor window, with nothing further to learn, the loop went on
screenshotting at 10 fps for as long as the model took to answer, competing with
the very request it was waiting for.

MEASURED 2026-09-13 on the Tier 3 board that broke the 20 s ceiling,
a GeeTest 3x3 photo grid on the python port:

    the board            1 distinct frame in 120 at 10 fps, MAD 0.0000 — still
    reported `burst`     12 113 ms  (the whole of `video_burst_max_ms`)
    reported as nothing  10 443 ms  (`fut.result()`, outside every phase)
    one model call       22.6 s

Two defects, one measurement. The camera ran eight seconds past the point it had
its answer, and the wait that actually cost the board its budget was attributed
to no phase at all — so the timing report read as "the burst is slow" when what
was slow was one inference.

THE JS PORT NEVER HAD EITHER. Its burst breaks on the settled window alone and
it names its own `inference` phase, which is why the same board reports
`burst 1.5s` + `inference 1.8s` there. This is the two ports agreeing again
rather than a new rule — CLAUDE.md 1c.

Nothing here weakens the cycle rule. `settled` still means a full floor window
with no new screen, which is longer than the worst dwell a real cycle holds a
screen for (geetest svg: p50 1.5 s, max 2.7 s, against a 4 s floor), so a board
that genuinely cycles never reaches it.
"""
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken.page_solver import PageSolver, PageSolverConfig  # noqa: E402

_STILL = b"one-screen-forever" + b"\x00" * 64


class _Kf:
    def __init__(self, n):
        self.mode = "still"
        self.steady_screens = 1
        self.frames = list(range(n))


def _run(monkeypatch, model_seconds):
    """One speculative solve against a board that never changes.

    Returns (frames_filmed, phases_seen). `model_seconds` is how long the
    inference takes — the whole question is whether it moves the first number.
    """
    import cv2
    import numpy as np

    solver = PageSolver(config=PageSolverConfig())
    solver._reset_animated_state()
    solver._deadline_ms = None

    shots = {"n": 0}

    def fake_shot(element, path, animations="allow", **_kw):
        shots["n"] += 1
        with open(path, "wb") as fh:
            fh.write(_STILL)

    def slow_model(*_a, **_kw):
        time.sleep(model_seconds)
        return [], []

    phases: list = []

    import contextlib

    @contextlib.contextmanager
    def recording_phase(name):
        phases.append(name)
        yield

    monkeypatch.setattr(solver, "_screenshot", fake_shot)
    monkeypatch.setattr(solver, "_get_solution", slow_model)
    monkeypatch.setattr(solver, "_phase", recording_phase)
    monkeypatch.setattr(cv2, "imread",
                        lambda p: np.zeros((4, 4, 3), dtype=np.uint8))
    monkeypatch.setattr("captchakraken.keyframes.extract_keyframes",
                        lambda frames, fps: _Kf(len(frames)))
    monkeypatch.setattr("captchakraken.keyframes.write_keyframes",
                        lambda kfset, d, stem: [])

    solver._speculate(object(), "shot.png", "unknown", None, False)
    return shots["n"], phases


def _floor_and_ceiling():
    cfg = PageSolverConfig()
    per_frame = 1000.0 / cfg.video_burst_fps
    return (round(cfg.video_burst_duration_ms / per_frame),
            round(cfg.video_burst_max_ms / per_frame))


def test_a_still_board_films_the_floor_however_slow_the_model_is(monkeypatch):
    floor, ceiling = _floor_and_ceiling()
    assert ceiling > floor, "this test is meaningless if the two are equal"
    # Comfortably longer than the floor window the burst films, so the old
    # `fut.done() and settled` would have run to the ceiling.
    filmed, _ = _run(monkeypatch, model_seconds=(floor / 10.0) + 1.5)
    assert filmed <= floor + 1, (
        f"a still board filmed {filmed} frames against a {floor}-frame floor "
        f"and a {ceiling}-frame ceiling — the burst is still waiting on the "
        f"model rather than on the board")


def test_the_floor_is_still_filmed(monkeypatch):
    """Not "stop as soon as it looks still": a clip shorter than the floor is one
    the slicer cannot read, and a cycle shorter than the floor would be missed."""
    floor, _ = _floor_and_ceiling()
    filmed, _ = _run(monkeypatch, model_seconds=0.0)
    assert filmed >= floor, f"filmed {filmed} frames, under the {floor}-frame floor"


def test_the_wait_for_the_model_is_named(monkeypatch):
    """The 10.4 s hole. A speculative solve waits for the answer after the burst
    has stopped, and that wait is the single largest thing a slow board spends
    its budget on — it has to appear in the report as `inference`, the same name
    the JS port gives it, or the two ports' timings cannot be compared."""
    _, phases = _run(monkeypatch, model_seconds=0.2)
    assert phases.count("burst") == 1, phases
    assert "inference" in phases, (
        f"the post-burst wait for the model is outside every phase: {phases}. "
        f"Its cost is then reported as nothing at all.")


def test_the_js_burst_does_not_wait_on_its_inference_either():
    """CLAUDE.md 1c, pinned against the source because Tier 1 has no node.

    The JS settled exit is a plain `break` on the floor window. If it ever grows
    a "and the answer is back" conjunct, the two ports diverge again — and the
    way that shows up is not a failure but a board that takes three times longer
    on one port than the other.
    """
    js = (Path(__file__).resolve().parents[2] / "js" / "src" / "solver.ts").read_text()
    marker = "elapsedMs - lastNewMs >= floorMs"
    assert marker in js, "the JS burst has lost its settled exit"
    line = next(l for l in js.splitlines() if marker in l)
    assert "done" not in line and "await" not in line, (
        f"the JS settled exit now waits on something: {line.strip()!r}")
