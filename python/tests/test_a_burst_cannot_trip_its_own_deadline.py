"""A full-length burst must not be killed by its own hang detector.

`_record_keyframes` plans `total` frames from the burst CEILING
(`video_burst_max_ms`) — 120 frames at the shipped 10fps/12s — and paces them
at `1000/fps`. Alongside that it arms a deadline meant to catch a HUNG
screenshot, not a slow one.

That deadline was computed from the burst FLOOR (`video_burst_duration_ms`,
4s): `3 * 4000 + 5000` = 17s, for a loop entitled to spend 12s pacing alone.
That leaves 17000/120 = 141ms per frame against a 100ms interval — about 40ms
of slack for a screenshot measured at 40ms, before the PNG write and the sha1.
So a burst that legitimately ran to its ceiling killed the whole attempt with
"the animated recording stalled", and the driver gate recorded it as a failed
solve.

Measured: 16 of the python port's 69 failed attempts in one full run, on
`botdetect_text`, `lemin_cropped`, `prosopo_grid_3x3`, `tencent_slide`,
`yidun_iconclick` and `yidun_pictureclick` — every one a STILL board. The JS
port has no such deadline, so it could not produce this failure at all, and the
two ports came out 20 points apart on the same fixtures.

The rule: a hang detector must be slack against the work it supervises. The
solve budget is what bounds real time; this only has to notice a screenshot
that never returns.
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken.page_solver import PageSolverConfig, burst_hang_deadline_ms  # noqa: E402


def _plan(cfg):
    """What `_record_keyframes` actually plans: frames, and the ms to pace them."""
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
    """The interval is the floor cost of a frame, not its whole cost.

    A frame also screenshots (~40ms measured), writes a PNG and hashes it. If
    the deadline only funds the interval, the detector is measuring the machine
    rather than the widget.
    """
    cfg = PageSolverConfig()
    total, pacing_ms = _plan(cfg)
    slack_per_frame = (burst_hang_deadline_ms(cfg) - pacing_ms) / total
    assert slack_per_frame >= 100.0, (
        f"only {slack_per_frame:.0f}ms per frame beyond the pacing interval — a "
        f"40ms screenshot plus a write plus a hash trips this on a healthy box")


def test_it_is_derived_from_the_ceiling_not_the_floor():
    """The floor is how SHORT a burst may be; the loop is sized by the ceiling.

    Pinned as a relationship rather than a number so retuning either constant
    keeps the detector correct instead of silently re-breaking it.
    """
    lo = PageSolverConfig(video_burst_duration_ms=4_000, video_burst_max_ms=12_000)
    hi = PageSolverConfig(video_burst_duration_ms=4_000, video_burst_max_ms=30_000)
    assert burst_hang_deadline_ms(hi) > burst_hang_deadline_ms(lo), (
        "raising only the CEILING lengthened the burst but not its deadline")


def test_it_still_fires_on_something_actually_hung():
    """Slack is not the absence of a limit."""
    cfg = PageSolverConfig()
    _, pacing_ms = _plan(cfg)
    assert burst_hang_deadline_ms(cfg) < 20 * pacing_ms, (
        "the detector is so loose it would sit through a genuinely hung "
        "screenshot for the whole solve budget")


def test_the_js_port_has_the_same_guard():
    """CLAUDE.md 1c. The JS port had NO hang detector, which is why this bug
    was invisible there and why the two ports disagreed by 20 points on the
    same fixtures — one killed the attempt, the other did not."""
    js = (Path(__file__).resolve().parents[2] / "js" / "src" / "solver.ts").read_text()
    assert "burstHangDeadlineMs" in js, (
        "the JS burst has no hang detector, so a screenshot that never returns "
        "runs until the solve budget ends")
    assert "videoBurstMaxMs" in js.split("burstHangDeadlineMs")[1][:400], (
        "the JS deadline must be sized off the CEILING too, or the two ports "
        "kill a burst at different lengths")
