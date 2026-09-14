import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken.page_solver import PageSolver, PageSolverConfig

_PNG_A = b"screen-A" + b"\x00" * 64
_PNG_B = b"screen-B" + b"\x11" * 64
_PNG_C = b"screen-C" + b"\x22" * 64


def _write(path, payload):
    with open(path, "wb") as fh:
        fh.write(payload)


def _solver(**kw):
    return PageSolver(config=PageSolverConfig(**kw))


def _frames_for(monkeypatch, payloads):
    import numpy as np

    solver = _solver()
    solver._reset_animated_state()
    solver._deadline_ms = None
    calls = {"n": 0}

    def fake_shot(element, path, animations="allow"):
        payload = payloads[calls["n"] % len(payloads)]
        calls["n"] += 1
        _write(path, payload)

    monkeypatch.setattr(solver, "_screenshot", fake_shot)
    import cv2
    monkeypatch.setattr(cv2, "imread", lambda p: np.zeros((4, 4, 3), dtype=np.uint8))
    monkeypatch.setattr("captchakraken.keyframes.extract_keyframes",
                        lambda frames, fps: _Kf(len(frames)))
    monkeypatch.setattr("captchakraken.keyframes.write_keyframes",
                        lambda kfset, d, stem: [])
    try:
        solver._record_keyframes(object())
    except Exception:
        pass
    return calls["n"]


class _Kf:
    def __init__(self, n):
        self.mode = "still"
        self.steady_screens = 1
        self.frames = list(range(n))


def test_a_board_that_never_changes_stops_at_the_burst_floor(monkeypatch):
    cfg = PageSolverConfig()
    floor = round(cfg.video_burst_duration_ms / (1000.0 / cfg.video_burst_fps))
    ceiling = round(cfg.video_burst_max_ms / (1000.0 / cfg.video_burst_fps))
    assert ceiling > floor, "this test is meaningless if the two are equal"

    n = _frames_for(monkeypatch, [_PNG_A])
    assert n <= floor + 1, (
        f"a still board filmed {n} frames; the floor is {floor} and the ceiling "
        f"{ceiling}. One screen can never close a cycle, so without a still exit "
        f"the recording always runs to the ceiling")


def test_the_floor_is_still_respected(monkeypatch):
    cfg = PageSolverConfig()
    floor = round(cfg.video_burst_duration_ms / (1000.0 / cfg.video_burst_fps))
    n = _frames_for(monkeypatch, [_PNG_A])
    assert n >= floor, f"filmed {n} frames, fewer than the {floor}-frame floor"


def test_the_js_port_has_the_same_still_exit():
    js = (Path(__file__).resolve().parents[2] / "js" / "src" / "solver.ts").read_text()
    assert "elapsedMs - lastNewMs >= floorMs" in js, (
        "the JS burst has no settled exit: a board that stops producing new "
        "screens can never close a cycle, so it films to videoBurstMaxMs while "
        "the python port stops once it has settled")


def test_a_board_that_moves_once_and_holds_also_stops(monkeypatch):
    cfg = PageSolverConfig()
    floor = round(cfg.video_burst_duration_ms / (1000.0 / cfg.video_burst_fps))
    ceiling = round(cfg.video_burst_max_ms / (1000.0 / cfg.video_burst_fps))
    payloads = [_PNG_A] + [_PNG_B] * (ceiling * 2)
    n = _frames_for(monkeypatch, payloads)
    assert n < ceiling, (
        f"a board that moved once and then held filmed {n} of {ceiling} frames "
        f"— it settled after one transition and the burst did not notice")
    assert n <= floor + 2, f"settled after the transition but filmed {n} frames"


def test_a_genuinely_cycling_board_is_not_cut_short(monkeypatch):
    cfg = PageSolverConfig()
    floor = round(cfg.video_burst_duration_ms / (1000.0 / cfg.video_burst_fps))
    payloads = ([_PNG_A] * 10 + [_PNG_B] * 10 + [_PNG_C] * 10) * 8
    n = _frames_for(monkeypatch, payloads)
    assert n >= floor, f"a cycling board stopped at {n} frames, under the floor"
