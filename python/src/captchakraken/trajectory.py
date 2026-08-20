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
