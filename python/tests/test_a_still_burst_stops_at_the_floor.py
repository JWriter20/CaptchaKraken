"""A recording of a board that never changes stops at the floor, not the ceiling.

`_record_keyframes` has exactly one early exit — `cycle_closed`, which needs a
digest to come back AFTER a different one. A still board only ever produces the
one digest, so it can never close a cycle and films every frame up to
`video_burst_max_ms`. `_speculate` has the missing half (`if not moved and
fut.done(): break`) because it has a concurrent inference to wait on; the
recording path had nothing equivalent.

MEASURED, because the obvious explanation was wrong. The first guess was render
noise defeating the byte hash, and it does not: driving the real fixtures
through camoufox and screenshotting one element 25 times at 10fps
(2026-09-09) —

    an hCaptcha click board           1 distinct sha1 / 25   MAD 0.0000
    an hCaptcha connect-the-path board             1 distinct sha1 / 25   MAD 0.0000
    an hCaptcha stacking animation              2 distinct sha1 / 25   MAD 32.51  (really moves)
    geetest_v4_svg                    3 distinct sha1 / 25   MAD  8.29  (really cycles)

Still frames really are byte-identical, so "one screen after the floor" is a
reliable still signal — and it is the same signal `_speculate` already trusts.
What it costs to lack it: 12s instead of 4s per escalation, on top of a
multi-image inference for a board with one keyframe in it. On the 6 Sep Tier 3
that was `tower_stack` 12.03s, `click_blocked_by_lines` 11.93s, `connect_path`
9.94s, with three of those types at 0% because the budget went on the film.
"""
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken.page_solver import PageSolver, PageSolverConfig  # noqa: E402

# Three distinct "screens". The BYTES are the whole point — they are what the
# burst takes its sha1 over — and they never have to decode, because the test
# stubs `cv2.imread` at the boundary where an image would be needed. Faking a
# valid PNG here would only be pretending the digest cares what it hashes.
_PNG_A = b"screen-A" + b"\x00" * 64
_PNG_B = b"screen-B" + b"\x11" * 64
_PNG_C = b"screen-C" + b"\x22" * 64


def _write(path, payload):
    with open(path, "wb") as fh:
        fh.write(payload)


def _solver(**kw):
    return PageSolver(config=PageSolverConfig(**kw))


def _frames_for(monkeypatch, payloads):
    """Run one recording where frame i gets `payloads[i % len(payloads)]`.

    Returns how many screenshots the burst asked for.
    """
    import numpy as np

    solver = _solver()
    solver._reset_animated_state()
    solver._deadline_ms = None
    calls = {"n": 0}

    # A real PNG per frame is what the loop hashes, but cv2 must also decode it,
    # so the image itself is faked at the decode boundary and the BYTES — the
    # thing the digest is taken over — are the part that varies.
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
    except Exception:  # noqa: BLE001 — the frame COUNT is what is under test
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
    """Not "stop as soon as it looks still" — a burst shorter than the floor is
    a clip the slicer cannot read, and CLAUDE.md's rule is that a burst must
    outlast one full cycle."""
    cfg = PageSolverConfig()
    floor = round(cfg.video_burst_duration_ms / (1000.0 / cfg.video_burst_fps))
    n = _frames_for(monkeypatch, [_PNG_A])
    assert n >= floor, f"filmed {n} frames, fewer than the {floor}-frame floor"


def test_the_js_port_has_the_same_still_exit():
    """CLAUDE.md 1c. Tier 3 drives BOTH ports through the same fixture, so a
    still exit in one and not the other does not show up as a bug — it shows up
    as the two ports disagreeing about how long a board takes, averaged.

    Pinned against the source because Tier 1 is hermetic and has no node.
    """
    js = (Path(__file__).resolve().parents[2] / "js" / "src" / "solver.ts").read_text()
    assert "captured - lastNewAt >= floorFrames" in js, (
        "the JS burst has no settled exit: a board that stops producing new "
        "screens can never close a cycle, so it films to videoBurstMaxMs while "
        "the python port stops once it has settled")


def test_a_board_that_moves_once_and_holds_also_stops(monkeypatch):
    """an hCaptcha stacking animation — two screens, neither repeating.

    A one-screen rule misses it entirely: it has moved, so it is not "still",
    and it never comes back to a screen it has shown, so the cycle never
    closes. Measured 2 distinct sha1 over 25 frames with MAD 32.51, and it ran
    the full 12s ceiling under the first version of this exit.
    """
    cfg = PageSolverConfig()
    floor = round(cfg.video_burst_duration_ms / (1000.0 / cfg.video_burst_fps))
    ceiling = round(cfg.video_burst_max_ms / (1000.0 / cfg.video_burst_fps))
    # One frame of screen A, then screen B forever: it transitions once.
    payloads = [_PNG_A] + [_PNG_B] * (ceiling * 2)
    n = _frames_for(monkeypatch, payloads)
    assert n < ceiling, (
        f"a board that moved once and then held filmed {n} of {ceiling} frames "
        f"— it settled after one transition and the burst did not notice")
    assert n <= floor + 2, f"settled after the transition but filmed {n} frames"


def test_a_genuinely_cycling_board_is_not_cut_short(monkeypatch):
    """The exit must not fire on a board still producing new screens.

    A real cycle holds a screen for at most 2.7s (measured on the geetest svg
    board), comfortably inside the 4s window, so it keeps producing something
    new and runs until its cycle CLOSES — which is the existing rule.
    """
    cfg = PageSolverConfig()
    floor = round(cfg.video_burst_duration_ms / (1000.0 / cfg.video_burst_fps))
    # A new screen every 10 frames (1s), cycling A,B,C — the cycle closes when
    # A comes back, which is the exit that should fire, not the settled one.
    payloads = ([_PNG_A] * 10 + [_PNG_B] * 10 + [_PNG_C] * 10) * 8
    n = _frames_for(monkeypatch, payloads)
    assert n >= floor, f"a cycling board stopped at {n} frames, under the floor"
