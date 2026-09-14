"""
Human-like mouse trajectories for the page driver.

The Python side of `js/src/trajectory.ts`; the two are one implementation in
two languages, pinned by `test_trajectory_parity.py`.

The CONTRACT is identical, which is what matters for keeping the two drivers in
step: given a start point, an end point and a sampling frequency, return
`(points, timings)` where `timings[i]` is the cumulative milliseconds from the
start of the gesture at which `points[i]` should be delivered. The caller
(`page_solver`) sleeps against those cumulative timings exactly as the TS driver
does, so movement pacing is driver-independent.

THE MOUSE PATH IS NOT OURS AND SHOULD NOT BE. It was a Bezier arc with a
Fitts's-law duration, an ease-in-out velocity profile, speed-scaled jitter and
an overshoot-and-correct — every one of those a MODEL of what a hand does,
tuned by hand, and each one a thing a detector can look for precisely because
it is a closed form. `cursory` does not model anything: it finds the closest
match in a database of thousands of trajectories recorded from real people,
morphs it onto the requested endpoints, and adds noise. The realism is
measured rather than asserted, and the failure mode is a bad recording rather
than a recognisable curve.

It also settles the dual-port problem for good. The two drivers used to carry
one algorithm written twice, pinned by a statistical parity test, because that
is the best two independent implementations can do. `cursory` and `cursory-js`
are a port rather than a rewrite and agree BIT FOR BIT on a seed — both reduce
to the same numpy PCG64 stream — so the two drivers now share a mouse rather
than resembling each other. Measured: seed 42 over (120, 80) -> (940, 560)
gives 48 identical points and identical timings in both.

`generate_swipe` below is OURS and stays ours: it is the same contract for a
FINGER, and Cursory records mice. A finger is not a slower mouse — different
velocity profile, different bow, a contact patch that wanders — so it is a
different model rather than this one with other constants. See its docstring.
"""

from __future__ import annotations

import math
import random
from typing import List, Optional, Sequence, Tuple

from cursory import generate_trajectory as cursory_trajectory

Point = Tuple[float, float]






#: How far a path may run past its endpoint, along the travel axis, before it is
#: redrawn. Small enough to keep the claim "this gesture does not overshoot"
#: true, large enough not to reject a path for a rounding artefact.
_OVERSHOOT_TOL_PX = 2.0

#: How many times to redraw before giving up and taking the best draw seen.
#: Never fails: a gesture that cannot find a clean path still moves.
_OVERSHOOT_REDRAWS = 6


def _overshoot(points: Sequence[Point], start: Point, end: Point) -> float:
    """How far past `end` the path travels, projected on the travel axis."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length == 0.0:
        return 0.0
    ux, uy = dx / length, dy / length
    return max(((x - start[0]) * ux + (y - start[1]) * uy)
               for x, y in points) - length


def _draw(start: Point, end: Point, frequency: int, frequency_randomizer: float,
          seed: Optional[int], directness: float, avoid_overshoot: bool):
    """One Cursory trajectory, redrawn until it does not run past its endpoint.

    REDRAWN, NOT RESHAPED. Cursory's output is a recording of a real movement
    morphed onto these endpoints, and the whole reason to use it is that nobody
    drew the curve. Clamping or straightening a path would put a hand-tuned
    step back in the middle of it; drawing a different one keeps every gesture a
    genuine recording.

    WHY ANY OF THEM RUN PAST. A recording carries whatever excursion the person
    made relative to their OWN endpoints, and morphing scales that onto ours —
    so a recording that wandered produces a short movement that swings well past
    the target and returns. Measured over 400 draws per distance: 6-25% run more
    than 2px past, worst at ~250px, with a tail reaching 200px past a 150px
    movement. For a pointer travelling to a click that is realistic and free.
    For a DRAG it is not: the pointer is carrying something, so the excursion
    drags the piece past the notch and back.

    `directness` cannot fix it — it selects among recordings rather than
    straightening one, so overshoot bottoms out around 12% at 0.65 (the default
    used here) and rises again toward 1.0.

    Costs 1.07-1.33 draws on average; six redraws leave under 0.03% unclean, and
    those take the least-overshooting draw rather than failing.

    A SEED STILL MEANS ONE TRAJECTORY. Redrawing derives its attempts from the
    seed, so a seeded call is reproducible and the cross-port fixture holds.
    """
    best = None
    best_over = None
    for attempt in range(_OVERSHOOT_REDRAWS if avoid_overshoot else 1):
        points, timings = cursory_trajectory(
            start, end,
            frequency=frequency,
            frequency_randomizer=frequency_randomizer,
            seed=None if seed is None else seed + attempt,
            directness=directness,
        )
        pts = [(float(x), float(y)) for x, y in points]
        if not avoid_overshoot:
            return pts, [float(t) for t in timings]
        over = _overshoot(pts, start, end)
        if over <= _OVERSHOOT_TOL_PX:
            return pts, [float(t) for t in timings]
        if best_over is None or over < best_over:
            best, best_over = (pts, [float(t) for t in timings]), over
    return best


def generate_trajectory(
    target_start: Sequence[float],
    target_end: Sequence[float],
    frequency: int = 60,
    frequency_randomizer: float = 1.0,
    seed: Optional[int] = None,
    directness: float = 0.65,
    avoid_overshoot: bool = False,
) -> Tuple[List[Point], List[float]]:
    """
    Returns `(points, timings)`. `timings` is CUMULATIVE milliseconds from the
    gesture start — not per-step deltas — because that is what both drivers'
    trace loops sleep against, and the two must pace identically.

    `frequency` is samples per second. `frequency_randomizer` is the largest
    jitter applied to each sample time, IN MILLISECONDS — it was a fraction
    while this was a curve, and the units changed with the model. `seed` makes a
    movement reproducible; production leaves it None, which draws fresh OS
    entropy per call. `directness` is the preference for shorter, straighter
    recordings, 0 to 1.

    A thin delegation, and deliberately thin: the arguments are Cursory's own,
    so there is no second set of knobs here to drift from the ones that do
    something.
    """
    return _draw((float(target_start[0]), float(target_start[1])),
                 (float(target_end[0]), float(target_end[1])),
                 frequency, frequency_randomizer, seed, directness,
                 avoid_overshoot)


# ── Touch ───────────────────────────────────────────────────────────────────
#
# The PATH is Cursory's, the same as the mouse. What stays here is the one thing
# a trajectory library cannot know, because it is not about the path at all: a
# touchscreen reports the CENTROID OF A CONTACT PATCH, and that centroid wanders
# under finger pressure independently of where the finger is going. A swipe
# whose samples sit exactly on a generated path is a swipe no digitizer produced.
#
# Everything else that used to live here — a Fitts's-law duration, an asymmetric
# ease, a bowed Bezier, a short-move threshold — was a MODEL of finger motion
# built from platform tap-target guidelines rather than from measurement, which
# is the same kind of hand-tuned closed form the mouse model was replaced for.
#
# MEASURED 2026-09-14 over captcha-sized drags, bow from the straight line at
# 90 Hz, against the model that used to be here:
#
#     80px    ours 6.0px    cursory 5.8px     equivalent
#     150px   ours 11.4px   cursory 23.7px    cursory arcs about twice as far
#     250px   ours 17.9px   cursory 24.3px
#
# So this is not a free swap and it is recorded as such: a finger on a wrist
# pivot plausibly bows less than a hand moving a mouse across a desk, and
# Cursory's `directness` does not tune it — it selects among RECORDINGS, so it
# does nothing at 150px and overshoots at 250px. What settles it is that our
# numbers were asserted and Cursory's are measured from real people; a
# hand-tuned constant is a fingerprint surface whoever wrote it.

#: The contact patch's centroid wander. `_TOUCH_WOBBLE_DECAY` is an AR(1)
#: coefficient, which is what makes the wander LOW-FREQUENCY: white per-sample
#: noise would show up in a spectrum as nothing a finger produces.
_TOUCH_WOBBLE_PX = 0.55
_TOUCH_WOBBLE_DECAY = 0.82


#: A FINGER PREFERS THE EFFICIENT RECORDINGS. Cursory's `directness` biases
#: selection toward shorter, straighter movements, and for touch that is worth
#: spending: a drag is the one gesture a person is watching happen, and a
#: wandering recording morphed onto a 150px slider reads as sluggish.
#:
#: MEASURED 2026-09-14 over 80/150/250px drags at 90 Hz, 600 draws per setting:
#:
#:     directness   duration p50   p90    bow p50   redraws
#:     0.65 (mouse)     519 ms    729 ms   11.7px    10.7%
#:     0.85 (here)      400 ms    729 ms    9.9px    10.8%
#:     0.90             400 ms    729 ms    9.8px    15.0%
#:
#: 0.85 takes the whole of the speed-up — 23% off the median, and 40% at 150px,
#: which is where a slider drag lives — while the bow gets slightly TIGHTER,
#: which is the direction a finger on a wrist pivot should go. 0.9 buys no more
#: time and costs half again as many redraws.
#:
#: The mouse stays at Cursory's own 0.65. Its overshoot floor is lowest there,
#: and a pointer merely travelling to a click has no reason to hurry.
_SWIPE_DIRECTNESS = 0.85


def generate_swipe(
    target_start: Sequence[float],
    target_end: Sequence[float],
    frequency: int = 90,
    frequency_randomizer: float = 1.0,
    seed: Optional[int] = None,
    directness: float = _SWIPE_DIRECTNESS,
) -> Tuple[List[Point], List[float]]:
    """A FINGER travelling from `target_start` to `target_end`.

    Same contract as `generate_trajectory`: `(points, timings)`, timings
    cumulative from 0, last point exactly `target_end`.

    Cursory's path, sampled faster — a digitizer reports at a higher and steadier
    rate than a mouse, so the default frequency is 90 rather than 60 — with the
    contact wobble laid over it. See the section comment above for what was
    removed and what the swap measurably costs.

    ONLY EVER CALLED WHILE THE FINGER IS DOWN. A tap generates no path at all:
    `MobileHumanizer.move` records the position and dispatches nothing when
    there is no contact, because there is no hover on a touchscreen.
    """
    # Always: `generate_swipe` is only ever called while the finger is down, so
    # every path it produces is a drag carrying something.
    points, timings = _draw(
        (float(target_start[0]), float(target_start[1])),
        (float(target_end[0]), float(target_end[1])),
        frequency, frequency_randomizer, seed, directness, True)
    out: List[Point] = []
    wob_x = wob_y = 0.0
    last = len(points) - 1
    for i, (x, y) in enumerate(points):
        if i == 0 or i == last:
            # The ends are where the touch lands and lifts; a wobble there moves
            # the gesture rather than texturing it.
            out.append((float(x), float(y)))
            continue
        wob_x = wob_x * _TOUCH_WOBBLE_DECAY + random.gauss(0.0, _TOUCH_WOBBLE_PX)
        wob_y = wob_y * _TOUCH_WOBBLE_DECAY + random.gauss(0.0, _TOUCH_WOBBLE_PX)
        out.append((float(x) + wob_x, float(y) + wob_y))
    return out, [float(t) for t in timings]
