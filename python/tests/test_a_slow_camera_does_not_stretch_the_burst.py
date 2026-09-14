"""`video_burst_duration_ms` is a WALL-CLOCK budget, and a slow camera cannot spend it.

Both burst loops ask the same question — "is this board moving on its own?" —
and both answer it by watching for a floor-length window. The window is what
makes the answer sound: it is longer than the worst dwell a real cycle holds a
screen for (max 2.7s measured on the geetest svg board), so a board that
genuinely cycles always produces a new screen inside it.

That is a statement about SECONDS. Counting frames instead only says the same
thing while the loop actually reaches `video_burst_fps`, and a loop that
screenshots slower than its interval never sleeps — so the window stretches by
exactly however slow the camera is, and the burst holds an answer it already
has for twice as long as it meant to.

Not hypothetical, and not evenly distributed. One element screenshot, same
fixture, same box:

    camoufox, desktop                    15.8 ms   → 40 frames = 4.02 s
    chromium, Pixel 7 (DPR 2.625)       183.5 ms   → 40 frames = 8.25 s

The DPR is the whole difference — the same viewport and touch emulation at DPR
1 costs 66.6 ms — so the mobile arm waited twice as long as the desktop arm for
the identical amount of evidence, while its inference had been sitting finished
since 2.15 s.

A virtual clock rather than real sleeps: the rule under test is "how much time
may pass", so a test that measured the box instead would be both slow and
flaky.
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken import page_solver  # noqa: E402
from captchakraken.page_solver import PageSolver, PageSolverConfig  # noqa: E402

# The BYTES are the point — they are what the burst takes its sha1 over. See
# test_a_still_burst_stops_at_the_floor for why they never have to decode.
_PNG_A = b"screen-A" + b"\x00" * 64
_PNG_B = b"screen-B" + b"\x11" * 64
_PNG_C = b"screen-C" + b"\x22" * 64


class _Clock:
    """Time only moves when the burst spends it: a frame, or a sleep."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, float(seconds))

    def tick(self, seconds: float) -> None:
        self.t += seconds


class _Kf:
    def __init__(self, n: int) -> None:
        self.mode = "static"
        self.steady_screens = 1
        self.frames = list(range(n))


def _burst(monkeypatch, payloads, frame_cost_ms, which="record", **cfg_kw):
    """Film one board through `which` loop with a camera costing `frame_cost_ms`.

    Returns `(frames_filmed, elapsed_ms)`.
    """
    import numpy as np

    clock = _Clock()
    monkeypatch.setattr(page_solver, "time", clock)

    solver = PageSolver(config=PageSolverConfig(**cfg_kw))
    solver._reset_animated_state()
    solver._deadline_ms = None
    calls = {"n": 0}

    def fake_shot(element, path, animations="allow", **kw):
        payload = payloads[calls["n"] % len(payloads)]
        calls["n"] += 1
        with open(path, "wb") as fh:
            fh.write(payload)
        clock.tick(frame_cost_ms / 1000.0)

    monkeypatch.setattr(solver, "_screenshot", fake_shot)
    import cv2
    monkeypatch.setattr(cv2, "imread", lambda p: np.zeros((4, 4, 3), dtype=np.uint8))
    monkeypatch.setattr("captchakraken.keyframes.extract_keyframes",
                        lambda frames, fps: _Kf(len(frames)))
    monkeypatch.setattr("captchakraken.keyframes.write_keyframes",
                        lambda kfset, d, stem: [])

    if which == "record":
        try:
            solver._record_keyframes(object())
        except Exception:  # noqa: BLE001 — the CLOCK is what is under test
            pass
    else:
        # `_speculate` films while an inference runs. The answer is irrelevant
        # here; what matters is that the camera, not the model, sets the pace.
        monkeypatch.setattr(solver, "_get_solution", lambda *a, **k: ([], []))
        try:
            solver._speculate(object(), "shot.png", "hcaptcha", None, False)
        except Exception:  # noqa: BLE001
            pass
    return calls["n"], clock.t * 1000.0


def test_a_slow_camera_does_not_stretch_the_still_window(monkeypatch):
    """The mobile arm's bug, at the mobile arm's measured frame cost."""
    cfg = PageSolverConfig()
    floor_ms = cfg.video_burst_duration_ms
    n, elapsed = _burst(monkeypatch, [_PNG_A], frame_cost_ms=183.5)
    assert elapsed <= floor_ms * 1.35, (
        f"a still board held the burst for {elapsed:.0f}ms against a "
        f"{floor_ms}ms floor ({n} frames). The window is counted in frames, so "
        f"a camera slower than the interval spends the budget it was given")


def test_a_slow_camera_does_not_stretch_the_speculative_window(monkeypatch):
    """Same rule in the loop that runs against a concurrent inference."""
    cfg = PageSolverConfig()
    floor_ms = cfg.video_burst_duration_ms
    n, elapsed = _burst(monkeypatch, [_PNG_A], frame_cost_ms=183.5,
                        which="speculate")
    assert elapsed <= floor_ms * 1.35, (
        f"the speculative burst held a still board for {elapsed:.0f}ms against "
        f"a {floor_ms}ms floor ({n} frames)")


def test_the_ceiling_is_wall_clock_too(monkeypatch):
    """A board that never settles stops at `video_burst_max_ms` of SECONDS.

    Three screens that never repeat: the cycle never closes, nothing ever
    settles, so the only exit is the ceiling — which is the case that reached
    31s on the mobile arm inside a 45s solve budget.
    """
    cfg = PageSolverConfig()
    ceiling_ms = cfg.video_burst_max_ms
    payloads = [_PNG_A, _PNG_B, _PNG_C] * 400
    n, elapsed = _burst(monkeypatch, payloads, frame_cost_ms=183.5)
    assert elapsed <= ceiling_ms * 1.2, (
        f"a board that never settles filmed for {elapsed:.0f}ms against a "
        f"{ceiling_ms}ms ceiling ({n} frames)")


def test_a_fast_camera_still_films_the_whole_window(monkeypatch):
    """The floor is a FLOOR. The desktop arm must not get a shorter look.

    A burst cut below `video_burst_duration_ms` is one that never outlasts a
    cycle's dwell, which is the failure the window exists to prevent — so
    making the rule wall-clock must not let a fast camera finish early.
    """
    cfg = PageSolverConfig()
    floor_ms = cfg.video_burst_duration_ms
    n, elapsed = _burst(monkeypatch, [_PNG_A], frame_cost_ms=15.8)
    assert elapsed >= floor_ms, (
        f"a still board was filmed for only {elapsed:.0f}ms against a "
        f"{floor_ms}ms floor ({n} frames)")


def test_a_cycling_board_is_still_judged_on_its_screens(monkeypatch):
    """Wall-clock bounds the CLIP, it does not decide what the clip means.

    A real cycle holds a screen at most 2.7s, so it keeps producing new screens
    inside the window and exits on `cycle_closed` — the rule that was always
    there, reached now in seconds rather than in frames.
    """
    cfg = PageSolverConfig()
    floor_ms = cfg.video_burst_duration_ms
    # A new screen every ~1s at the slow camera's rate, cycling A,B,C.
    payloads = ([_PNG_A] * 5 + [_PNG_B] * 5 + [_PNG_C] * 5) * 40
    n, elapsed = _burst(monkeypatch, payloads, frame_cost_ms=183.5)
    assert elapsed >= floor_ms, (
        f"a cycling board was cut at {elapsed:.0f}ms, under the {floor_ms}ms floor")
    assert elapsed <= cfg.video_burst_max_ms * 1.2, (
        f"a cycling board filmed {elapsed:.0f}ms; it closes its cycle well "
        f"inside the ceiling")


def test_the_js_port_counts_the_same_window_in_milliseconds():
    """CLAUDE.md 1c. Tier 3 drives both ports through one fixture, so a window
    that means seconds in one port and frames in the other does not read as a
    bug — it reads as the two ports disagreeing about how long a board takes.

    Pinned against the source because Tier 1 is hermetic and has no node.
    """
    js = (Path(__file__).resolve().parents[2] / "js" / "src" / "solver.ts").read_text()
    assert "elapsedMs - lastNewMs >= floorMs" in js, (
        "the JS burst still counts its settled window in frames: a camera "
        "slower than the interval stretches the window there while the python "
        "port holds it to videoBurstDurationMs")
