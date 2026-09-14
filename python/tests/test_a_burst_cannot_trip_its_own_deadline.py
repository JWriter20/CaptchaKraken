"""The hang detector must be slack against the work it supervises: 16 of the Python port's 69 failed attempts were this deadline firing on a still board."""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken.page_solver import PageSolverConfig, burst_hang_deadline_ms


def _plan(cfg):
    fps = max(1, int(cfg.video_burst_fps))
    interval = 1000.0 / fps
    floor_frames = max(1, round(cfg.video_burst_duration_ms / interval))
    total = max(floor_frames, round(cfg.video_burst_max_ms / interval))
    return total, total * interval


def test_the_deadline_clears_a_full_length_burst():
    cfg = PageSolverConfig()
    total, pacing_ms = _plan(cfg)
    budget = burst_hang_deadline_ms(cfg)
    assert budget > pacing_ms, (
        f"a burst may run {total} frames = {pacing_ms:.0f}ms of pacing alone, "
        f"and the hang detector fires at {budget:.0f}ms")


def test_every_frame_gets_real_room_beyond_its_interval():
    cfg = PageSolverConfig()
    total, pacing_ms = _plan(cfg)
    slack_per_frame = (burst_hang_deadline_ms(cfg) - pacing_ms) / total
    assert slack_per_frame >= 100.0, (
        f"only {slack_per_frame:.0f}ms per frame beyond the pacing interval — a "
        f"40ms screenshot plus a write plus a hash trips this on a healthy box")


def test_it_is_derived_from_the_ceiling_not_the_floor():
    lo = PageSolverConfig(video_burst_duration_ms=4_000, video_burst_max_ms=12_000)
    hi = PageSolverConfig(video_burst_duration_ms=4_000, video_burst_max_ms=30_000)
    assert burst_hang_deadline_ms(hi) > burst_hang_deadline_ms(lo), (
        "raising only the CEILING lengthened the burst but not its deadline")


def test_it_still_fires_on_something_actually_hung():
    cfg = PageSolverConfig()
    _, pacing_ms = _plan(cfg)
    assert burst_hang_deadline_ms(cfg) < 20 * pacing_ms, (
        "the detector is so loose it would sit through a genuinely hung "
        "screenshot for the whole solve budget")


def test_the_js_port_has_the_same_guard():
    js = (Path(__file__).resolve().parents[2] / "js" / "src" / "solver.ts").read_text()
    assert "burstHangDeadlineMs" in js, (
        "the JS burst has no hang detector, so a screenshot that never returns "
        "runs until the solve budget ends")
    assert "videoBurstMaxMs" in js.split("burstHangDeadlineMs")[1][:400], (
        "the JS deadline must be sized off the CEILING too, or the two ports "
        "kill a burst at different lengths")
