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

What makes the output human rather than a lerp:
  - a Bezier arc, not a straight line (hand motion bows)
  - Fitts's-law duration: distance and precision set the time, not a constant
  - an ease-in-out velocity profile, so the cursor accelerates then brakes
  - sub-pixel jitter that scales with speed (fast motion is sloppier)
  - a small overshoot-and-correct on longer moves, which is the single most
    recognisable human tell and the one a linear interpolation never produces

`generate_swipe` is the SAME contract for a FINGER, and it is a different model
rather than the mouse model with different constants — see its docstring.
"""

from __future__ import annotations

import math
import random
from typing import List, Sequence, Tuple

Point = Tuple[float, float]

# Fitts's law: MT = a + b * log2(distance / width + 1). The constants are the
# usual empirical range for a mouse; `_WIDTH` stands in for target precision.
_FITTS_A_MS = 90.0
_FITTS_B_MS = 105.0
_WIDTH_PX = 28.0

# Below this, a move is a nudge inside one element: no overshoot, no arc.
_SHORT_MOVE_PX = 24.0
# Overshooting a 30px hop looks like a twitch, not a human.
_OVERSHOOT_MIN_DISTANCE_PX = 220.0


def _ease_in_out(t: float) -> float:
    """Cubic ease. Real pointer motion is roughly bell-shaped in velocity."""
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0


def _cubic_bezier(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    u = 1.0 - t
    x = u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0]
    y = u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]
    return (x, y)


def _control_points(start: Point, end: Point, distance: float) -> Tuple[Point, Point]:
    """
    Two control points offset PERPENDICULAR to the travel direction, so the path
    bows to one side. The sign is random per gesture: always bowing the same way
    would itself be a fingerprint.
    """
    dx, dy = end[0] - start[0], end[1] - start[1]
    if distance == 0:
        return start, end
    # Unit normal to the direction of travel.
    nx, ny = -dy / distance, dx / distance
    # Bow proportional to distance, but capped — long drags don't arc absurdly.
    bow = min(distance * random.uniform(0.06, 0.16), 140.0) * random.choice((-1.0, 1.0))
    # Control points at roughly 1/3 and 2/3 along, each pushed off-axis.
    c1 = (
        start[0] + dx * random.uniform(0.20, 0.40) + nx * bow,
        start[1] + dy * random.uniform(0.20, 0.40) + ny * bow,
    )
    c2 = (
        start[0] + dx * random.uniform(0.60, 0.80) + nx * bow * random.uniform(0.4, 0.9),
        start[1] + dy * random.uniform(0.60, 0.80) + ny * bow * random.uniform(0.4, 0.9),
    )
    return c1, c2


def generate_trajectory(
    target_start: Sequence[float],
    target_end: Sequence[float],
    frequency: int = 60,
    frequency_randomizer: float = 0.12,
) -> Tuple[List[Point], List[float]]:
    """
    Returns `(points, timings)`. `timings` is CUMULATIVE milliseconds from the
    gesture start — not per-step deltas — because that is what the TS driver's
    `tracePath` sleeps against, and the two must pace identically.

    `frequency` is samples per second (the TS driver passes 60).
    `frequency_randomizer` jitters each frame's timestamp so the sample interval
    is not a metronome; a perfectly periodic mousemove stream is trivially
    detectable.
    """
    start: Point = (float(target_start[0]), float(target_start[1]))
    end: Point = (float(target_end[0]), float(target_end[1]))

    dx, dy = end[0] - start[0], end[1] - start[1]
    distance = math.hypot(dx, dy)

    if distance < 1e-6:
        return [end], [0.0]

    duration_ms = _FITTS_A_MS + _FITTS_B_MS * math.log2(distance / _WIDTH_PX + 1.0)
    duration_ms *= random.uniform(0.85, 1.20)

    steps = max(2, int(round(duration_ms / 1000.0 * max(1, frequency))))

    # A short hop is a straight ease with jitter — bowing a 15px move looks worse
    # than not bowing it.
    if distance < _SHORT_MOVE_PX:
        c1, c2 = start, end
    else:
        c1, c2 = _control_points(start, end, distance)

    # Overshoot: on a long move the hand arrives past the target and corrects.
    # Modelled as a longer primary gesture to a point beyond the target, then a
    # short settle back — the settle is appended as extra samples below.
    overshoot = distance >= _OVERSHOOT_MIN_DISTANCE_PX and random.random() < 0.55
    aim: Point = end
    if overshoot:
        over = random.uniform(0.01, 0.035) * distance
        aim = (end[0] + dx / distance * over, end[1] + dy / distance * over)

    points: List[Point] = []
    timings: List[float] = []
    elapsed = 0.0
    per_step = duration_ms / steps

    for i in range(steps + 1):
        t = _ease_in_out(i / steps)
        px, py = _cubic_bezier(start, c1, c2, aim, t)

        # Jitter scales with instantaneous speed: the faster the cursor is
        # moving, the less precisely a human tracks the intended path.
        speed = abs(_ease_in_out(min(1.0, (i + 1) / steps)) - _ease_in_out(i / steps))
        jitter = min(1.6, 0.25 + speed * steps * 0.5)
        px += random.gauss(0.0, jitter * 0.5)
        py += random.gauss(0.0, jitter * 0.5)

        points.append((px, py))
        timings.append(elapsed)
        elapsed += per_step * random.uniform(1.0 - frequency_randomizer, 1.0 + frequency_randomizer)

    if overshoot:
        # Correction: a couple of small samples back onto the true target, at the
        # slower pace of a deliberate fine adjustment.
        settle_steps = random.randint(2, 4)
        origin = points[-1]
        for i in range(1, settle_steps + 1):
            t = i / settle_steps
            points.append(
                (
                    origin[0] + (end[0] - origin[0]) * t + random.gauss(0.0, 0.35),
                    origin[1] + (end[1] - origin[1]) * t + random.gauss(0.0, 0.35),
                )
            )
            timings.append(elapsed)
            elapsed += random.uniform(14.0, 30.0)

    # Land exactly on the requested pixel. Everything above is texture; the final
    # sample must be the point the caller asked for or clicks drift off-target.
    points[-1] = end
    return points, timings


# ── Touch ───────────────────────────────────────────────────────────────────
#
# Fitts's law holds for direct touch too, but with a slower intercept and a much
# wider effective target: a fingertip contact patch is ~9mm across, which is the
# 44pt / 48dp minimum both platform guidelines are built around. So the same
# distance takes longer AND is aimed less precisely than with a mouse.
_TOUCH_FITTS_A_MS = 160.0
_TOUCH_FITTS_B_MS = 135.0
_TOUCH_WIDTH_PX = 44.0

# How flick-like the launch is. 0 would be the mouse's symmetric bell; 1 would
# be an instantaneous jump at t=0. See `_ease_touch`.
_TOUCH_LAUNCH = 0.7

# A finger drags across glass on a short wrist/thumb pivot, so it bows far less
# than a hand moving a mouse across a desk.
_TOUCH_BOW_CAP_PX = 40.0

# The reported contact point wanders, because it is the CENTROID of a soft patch
# rolling under pressure rather than a rigid sensor. `_TOUCH_WOBBLE_DECAY` is the
# AR(1) coefficient that makes that wander low-frequency: white per-sample noise
# would show up in a spectrum as nothing a finger produces.
_TOUCH_WOBBLE_PX = 0.55
_TOUCH_WOBBLE_DECAY = 0.82


def _ease_touch(t: float) -> float:
    """Asymmetric ease: a finger leaves fast and brakes late.

    A mouse hand is symmetric (`_ease_in_out` — accelerate, decelerate, equally).
    A finger is not: the launch is a flick off the contact point and the arrival
    is a brake. Blending the two rather than using a pure ease-out is what keeps
    the launch from being an instantaneous jump at t=0, which no digitizer would
    ever report.
    """
    return (1.0 - _TOUCH_LAUNCH) * _ease_in_out(t) + _TOUCH_LAUNCH * (1.0 - (1.0 - t) ** 2.4)


def generate_swipe(
    target_start: Sequence[float],
    target_end: Sequence[float],
    frequency: int = 90,
    frequency_randomizer: float = 0.10,
) -> Tuple[List[Point], List[float]]:
    """A FINGER travelling from `target_start` to `target_end`, same contract as
    `generate_trajectory`: `(points, timings)`, timings cumulative from 0, last
    point exactly `target_end`.

    A separate model rather than `generate_trajectory` with other constants,
    because the three things that make the mouse output human are each WRONG
    here:

      - **No overshoot-and-correct.** That tell is a hand arriving past a target
        it cannot see under the cursor. A finger occludes its own target and
        commits; the correction, when there is one, is a second gesture.
      - **A different velocity profile.** `_ease_touch`, not `_ease_in_out` —
        see above.
      - **Different jitter.** The mouse model adds white noise scaled by speed
        (a hand tracking a path imprecisely). A digitizer instead reports a
        wandering centroid, which is low-frequency and roughly speed
        INDEPENDENT, so this is a smoothed random walk.

    `frequency` defaults to 90: digitizers sample at 120Hz+, but touchmove is
    coalesced to the compositor, so more samples than this buys nothing and
    costs a dispatch round-trip each.
    """
    start: Point = (float(target_start[0]), float(target_start[1]))
    end: Point = (float(target_end[0]), float(target_end[1]))

    dx, dy = end[0] - start[0], end[1] - start[1]
    distance = math.hypot(dx, dy)

    if distance < 1e-6:
        return [end], [0.0]

    duration_ms = _TOUCH_FITTS_A_MS + _TOUCH_FITTS_B_MS * math.log2(
        distance / _TOUCH_WIDTH_PX + 1.0
    )
    duration_ms *= random.uniform(0.85, 1.20)

    steps = max(2, int(round(duration_ms / 1000.0 * max(1, frequency))))

    if distance < _SHORT_MOVE_PX:
        c1, c2 = start, end
    else:
        c1, c2 = _control_points(start, end, distance)
        # Pull the mouse model's bow in: `_control_points` is shared, and its
        # arc is sized for a hand crossing a desk.
        shrink = min(1.0, _TOUCH_BOW_CAP_PX / max(_TOUCH_BOW_CAP_PX, distance * 0.16))
        c1 = (start[0] + (c1[0] - start[0]) * shrink, start[1] + (c1[1] - start[1]) * shrink)
        c2 = (start[0] + (c2[0] - start[0]) * shrink, start[1] + (c2[1] - start[1]) * shrink)

    points: List[Point] = []
    timings: List[float] = []
    elapsed = 0.0
    per_step = duration_ms / steps
    wob_x = wob_y = 0.0

    for i in range(steps + 1):
        t = _ease_touch(i / steps)
        px, py = _cubic_bezier(start, c1, c2, end, t)

        wob_x = wob_x * _TOUCH_WOBBLE_DECAY + random.gauss(0.0, _TOUCH_WOBBLE_PX)
        wob_y = wob_y * _TOUCH_WOBBLE_DECAY + random.gauss(0.0, _TOUCH_WOBBLE_PX)

        points.append((px + wob_x, py + wob_y))
        timings.append(elapsed)
        elapsed += per_step * random.uniform(1.0 - frequency_randomizer, 1.0 + frequency_randomizer)

    # Land exactly where the caller asked, same reason as the mouse model.
    points[-1] = end
    return points, timings
