# A board that pauses is not a board that has stopped.
#
# Two independent faults let `hcaptcha_item_animal_never_touches` be answered from one screen, and
# the gate's verdict for it was "THE DRIVER NEVER ASKED ABOUT THE KEYFRAMES — 0 keyframe calls, on
# an animated fixture. The answer describes one screen of a board that cycles."
#
# 1. THE MOVER WAS TOO SMALL TO SEE. `settle_diff_threshold` was 0.01 — a board is moving when 1% of
#    its pixels change. A bee crossing a meadow is 0.4% of the frame. Measured with the solver's own
#    `movement_ratio`, 220ms apart: the bee peaks at 0.00429 and NOT ONE of 55 polls cleared 0.01,
#    while rotating_obj (0.01845) and tile_flip (0.05863) cleared it easily. Every still board on the
#    corpus measured exactly 0.00000, so the threshold had roughly a decade of unused headroom.
#
# 2. A PAUSE WAS READ AS A SETTLE. `settle_frames` is 2, so 440ms of quiet ends the check. The bee
#    lands on a flower and sits. Captured at 220ms over 12s the board reads
#
#        .M...........MMMMMMM.......MMMMMMM........MMMM
#
#    and the leading `..` returns SETTLED before a single move has been seen. Fixing the threshold
#    alone changes nothing, which is why both are here: measured on the live fixture, lowering it to
#    0.002 left the verdict SETTLED.
#
# The traces below are that capture, resampled at the poll interval — not invented shapes.
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken.page_solver import PageSolverConfig, SettleVerdict, settle_verdict  # noqa: E402

POLL_MS = 220.0

#: hcaptcha_item_animal_never_touches@20260732, 12s at 220ms, threshold 0.002. Bursts and pauses.
BEE = ".M...........MMMMMMM.......MMMMMMM........MMMM"
#: The SAME type on the two seeds the gate actually drives, captured the same way. A different
#: shape — runs of five with five-poll gaps rather than sevens with elevens — and `x` is a
#: screenshot that failed and yielded no sample, which is what a loaded box does to this check.
#: Both read SETTLED under the old rule, which is the bug, on the seeds the gate scores.
BEE_SEED_30 = "xxxM....MMMMM.....MMMMM.....MMMMM.....MM."
BEE_SEED_31 = "xxxM....MMMMM.....MMMMM.....MMMMM.....M.M"
#: The same board at the OLD 0.01 threshold: the bee is below it everywhere.
BEE_AT_OLD_THRESHOLD = "." * 46
#: yandex_text@20260730 over the same window — a real still, max ratio 0.00000.
STILL = "." * 46
#: A board that paints and then stops. The paint is real motion and must not cost the solve.
PAINTS_THEN_STOPS = "MM" + "." * 44
#: rotating_obj_video: changes every 133-171ms, never pauses.
CONTINUOUS = "M" * 46


def _verdict(trace, cfg=None, poll_ms=POLL_MS):
    cfg = cfg or PageSolverConfig()
    samples = [((i + 1) * poll_ms, c == "M") for i, c in enumerate(trace)]
    return settle_verdict(samples, settle_frames=cfg.settle_frames,
                          animated_after_ms=cfg.animated_challenge_after_ms,
                          motion_streak=cfg.animated_motion_streak,
                          quiet_after_motion=cfg.settle_frames_after_motion)


# ── the board this was written for ──────────────────────────────────────────


@pytest.mark.parametrize("trace", [BEE, BEE_SEED_30, BEE_SEED_31],
                         ids=["seed32", "seed30", "seed31"])
def test_every_captured_seed_of_that_board_is_animated(trace):
    """All three seeds, captured off the live fixture. Seeds 30 and 31 are the ones the gate
    drives, and both settled under the old rule."""
    assert _verdict_skipping_failures(trace) == SettleVerdict.ANIMATED


def _verdict_skipping_failures(trace, cfg=None, poll_ms=316.0):
    """`x` is a screenshot that failed, which yields no sample at all — exactly as `_poll` behaves."""
    cfg = cfg or PageSolverConfig()
    samples, t = [], 0.0
    for c in trace:
        t += poll_ms
        if c == "x":
            continue
        samples.append((t, c == "M"))
    return settle_verdict(samples, settle_frames=cfg.settle_frames,
                          animated_after_ms=cfg.animated_challenge_after_ms,
                          motion_streak=cfg.animated_motion_streak,
                          quiet_after_motion=cfg.settle_frames_after_motion)


def test_a_bee_that_lands_between_flights_is_animated():
    assert _verdict(BEE) == SettleVerdict.ANIMATED, (
        "the board was answered from one screen; the whole puzzle is what the bee does over time"
    )


def test_the_longest_pause_in_any_capture_does_not_settle_it():
    """The quiet run has to outlast the real gap between the bee's flights: 11 polls on seed 32,
    5 on the two seeds the gate drives. 12 is the smallest value that survives all three, so the
    margin is thin by construction and lowering it puts the board back on the still path."""
    cfg = PageSolverConfig()
    longest = max(max(len(run) for run in trace.replace("x", "").split("M"))
                  for trace in (BEE, BEE_SEED_30, BEE_SEED_31))
    assert longest == 11, f"the captures changed; longest quiet run is now {longest}"
    assert cfg.settle_frames_after_motion > longest, (
        f"a {cfg.settle_frames_after_motion}-poll quiet run settles inside the bee's own pause"
    )


# ── the two faults, each shown to be necessary ──────────────────────────────


def test_the_threshold_alone_would_not_have_been_enough():
    """Measured on the live fixture: at 0.002 with the OLD 2-poll rule it still read SETTLED."""
    samples = [((i + 1) * POLL_MS, c == "M") for i, c in enumerate(BEE)]
    assert settle_verdict(samples, settle_frames=2, animated_after_ms=4500,
                          motion_streak=5, quiet_after_motion=0) == SettleVerdict.SETTLED


def test_the_rule_alone_would_not_have_been_enough():
    """At the old threshold the bee is invisible, so no rule over those samples can see it."""
    assert _verdict(BEE_AT_OLD_THRESHOLD) == SettleVerdict.SETTLED


def test_the_threshold_leaves_the_measured_still_boards_alone():
    cfg = PageSolverConfig()
    assert cfg.settle_diff_threshold == 0.002
    assert cfg.settle_diff_threshold > 0.001, "at or under the measured noise floor"


# ── what must not regress ───────────────────────────────────────────────────


def test_a_still_board_settles_as_fast_as_it_ever_did():
    """A board that never moved pays nothing: the longer quiet run is only for one that has."""
    cfg = PageSolverConfig()
    samples = [((i + 1) * POLL_MS, False) for i in range(20)]
    assert settle_verdict(samples, settle_frames=cfg.settle_frames,
                          animated_after_ms=cfg.animated_challenge_after_ms,
                          motion_streak=cfg.animated_motion_streak,
                          quiet_after_motion=cfg.settle_frames_after_motion) == SettleVerdict.SETTLED
    # and it settles on the SECOND poll, not the twelfth
    short = [((i + 1) * POLL_MS, False) for i in range(cfg.settle_frames)]
    assert settle_verdict(short, settle_frames=cfg.settle_frames,
                          animated_after_ms=cfg.animated_challenge_after_ms,
                          motion_streak=cfg.animated_motion_streak,
                          quiet_after_motion=cfg.settle_frames_after_motion) == SettleVerdict.SETTLED


def test_a_board_that_paints_and_stops_still_settles():
    assert _verdict(PAINTS_THEN_STOPS) == SettleVerdict.SETTLED


def test_a_continuous_animation_is_still_caught_early():
    """`motion_streak` is what keeps rotating_obj from spending 4.5s proving the obvious."""
    assert _verdict(CONTINUOUS) == SettleVerdict.ANIMATED


def test_a_real_still_is_never_called_animated():
    assert _verdict(STILL) == SettleVerdict.SETTLED


@pytest.mark.parametrize("trace, want", [
    (BEE, SettleVerdict.ANIMATED),
    (STILL, SettleVerdict.SETTLED),
    (PAINTS_THEN_STOPS, SettleVerdict.SETTLED),
    (CONTINUOUS, SettleVerdict.ANIMATED),
])
def test_the_four_shapes_together(trace, want):
    assert _verdict(trace) == want


def test_a_caller_that_does_not_ask_for_it_gets_the_old_rule():
    """`quiet_after_motion=0` is off, so an embedder pinned to the old behaviour keeps it."""
    samples = [((i + 1) * POLL_MS, c == "M") for i, c in enumerate(BEE)]
    assert settle_verdict(samples, settle_frames=2, animated_after_ms=4500,
                          motion_streak=5) == SettleVerdict.SETTLED
