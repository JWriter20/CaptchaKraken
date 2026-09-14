from __future__ import annotations

import time

import pytest

from captchakraken.page_solver import CaptchaSolveError, PageSolver, PageSolverConfig


FAST_BURST = {"video_burst_duration_ms": 200, "video_burst_fps": 10}


def _solver(**overrides) -> PageSolver:
    return PageSolver(config=PageSolverConfig(**{**FAST_BURST, **overrides}))


class _Element:

    def __init__(self) -> None:
        self.shots = 0

    def screenshot(self, **kwargs) -> None:
        self.shots += 1


def test_the_budget_is_derived_from_what_a_recording_actually_costs():
    cfg = PageSolverConfig()
    assert cfg.video_budget_ms() == (cfg.video_burst_max_ms
                                     + cfg.keyframe_wait_timeout_ms
                                     + cfg.video_extra_inference_ms)

    longer = PageSolverConfig(video_burst_max_ms=cfg.video_burst_max_ms * 2)
    assert longer.video_budget_ms() - cfg.video_budget_ms() == cfg.video_burst_max_ms


def test_recording_extends_the_deadline_once_and_only_once(monkeypatch):
    solver = _solver()
    solver._reset_animated_state()
    start = time.monotonic() * 1000.0
    solver._deadline_ms = start + solver.config.overall_solve_timeout_ms
    monkeypatch.setattr(solver, "_screenshot", lambda *a, **k: None)

    for _ in range(3):
        with pytest.raises(Exception):
            solver._record_keyframes(_Element())

    granted = solver._deadline_ms - (start + solver.config.overall_solve_timeout_ms)
    assert round(granted) == solver.config.video_budget_ms()


def test_a_caller_who_turned_recording_off_gets_no_extension(monkeypatch):
    solver = _solver(video_solve_enabled=False)
    solver._reset_animated_state()
    start = time.monotonic() * 1000.0
    solver._deadline_ms = start + solver.config.overall_solve_timeout_ms
    monkeypatch.setattr(solver, "_screenshot", lambda *a, **k: None)

    with pytest.raises(Exception):
        solver._record_keyframes(_Element())
    assert solver._deadline_ms == start + solver.config.overall_solve_timeout_ms


def test_a_burst_is_never_abandoned_partway_for_being_over_budget(monkeypatch):
    solver = _solver()
    solver._reset_animated_state()
    solver._deadline_ms = time.monotonic() * 1000.0 - 10_000
    solver._video_budget_granted = True

    element = _Element()
    monkeypatch.setattr(solver, "_screenshot", lambda *a, **k: element.screenshot())

    with pytest.raises(CaptchaSolveError) as excinfo:
        solver._record_keyframes(element)

    assert element.shots == 0
    assert "not starting one that would be cut off" in str(excinfo.value)
    assert "overall_solve_timeout_ms" in str(excinfo.value)


def test_the_default_budget_is_enough_for_an_escalation_on_the_last_round():
    cfg = PageSolverConfig()
    spent_on_rounds = (cfg.max_solve_loops - 1) * 7_000
    left = cfg.overall_solve_timeout_ms - spent_on_rounds + cfg.video_budget_ms()
    assert left >= cfg.video_burst_duration_ms + cfg.keyframe_wait_timeout_ms
