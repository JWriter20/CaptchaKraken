/**
 * Human-like mouse trajectories for the page driver.
 *
 * The TypeScript side of `python/src/captchakraken/trajectory.py`; the two are
 * one implementation in two languages, pinned by `test_trajectory_parity.py`.
 *
 * What makes the output human rather than a lerp:
 *   - a Bezier arc, not a straight line (hand motion bows)
 *   - Fitts's-law duration: distance and precision set the time, not a constant
 *   - an ease-in-out velocity profile, so the cursor accelerates then brakes
 *   - sub-pixel jitter that scales with speed (fast motion is sloppier)
 *   - a small overshoot-and-correct on longer moves, which is the single most
 *     recognisable human tell and the one a linear interpolation never produces
 *
 * Camoufox's own `humanize` is a DIFFERENT, browser-level mechanism that
 * re-humanises every `mouse.move()` it is handed. Running both composes them —
 * measured 82.1s vs 13.4s on one geetest_v4_slide solve, because each of the 60
 * points below became its own humanised sub-trajectory. Drive with humanize off.
 */

export type Point = [number, number];

// Fitts's law: MT = a + b * log2(distance / width + 1). The constants are the
// usual empirical range for a mouse; `WIDTH` stands in for target precision.
const FITTS_A_MS = 90.0;
const FITTS_B_MS = 105.0;
const WIDTH_PX = 28.0;

// Below this, a move is a nudge inside one element: no overshoot, no arc.
const SHORT_MOVE_PX = 24.0;
// Overshooting a 30px hop looks like a twitch, not a human.
const OVERSHOOT_MIN_DISTANCE_PX = 220.0;

/** Cubic ease. Real pointer motion is roughly bell-shaped in velocity. */
function easeInOut(t: number): number {
  if (t < 0.5) return 4.0 * t * t * t;
  return 1.0 - Math.pow(-2.0 * t + 2.0, 3) / 2.0;
}

function cubicBezier(p0: Point, p1: Point, p2: Point, p3: Point, t: number): Point {
  const u = 1.0 - t;
  const x = u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0];
  const y = u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1];
  return [x, y];
}

function uniform(lo: number, hi: number): number {
  return lo + Math.random() * (hi - lo);
}

/**
 * Box-Muller. `Math.random()` is uniform and the jitter model wants a normal
 * deviate; a uniform one has hard edges that show up as a boxy distribution of
 * off-path error, which is itself a fingerprint.
 */
function gauss(mu: number, sigma: number): number {
  let u = 0;
  while (u === 0) u = Math.random(); // log(0) is -Infinity
  const v = Math.random();
  return mu + sigma * Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
}

/**
 * Two control points offset PERPENDICULAR to the travel direction, so the path
 * bows to one side. The sign is random per gesture: always bowing the same way
 * would itself be a fingerprint.
 */
function controlPoints(start: Point, end: Point, distance: number): [Point, Point] {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  if (distance === 0) return [start, end];
  // Unit normal to the direction of travel.
  const nx = -dy / distance;
  const ny = dx / distance;
  // Bow proportional to distance, but capped — long drags don't arc absurdly.
  const bow = Math.min(distance * uniform(0.06, 0.16), 140.0) * (Math.random() < 0.5 ? -1.0 : 1.0);
  // Control points at roughly 1/3 and 2/3 along, each pushed off-axis.
  const c1: Point = [
    start[0] + dx * uniform(0.2, 0.4) + nx * bow,
    start[1] + dy * uniform(0.2, 0.4) + ny * bow,
  ];
  const c2: Point = [
    start[0] + dx * uniform(0.6, 0.8) + nx * bow * uniform(0.4, 0.9),
    start[1] + dy * uniform(0.6, 0.8) + ny * bow * uniform(0.4, 0.9),
  ];
  return [c1, c2];
}

/**
 * Returns `[points, timings]`. `timings` is CUMULATIVE milliseconds from the
 * gesture start — not per-step deltas — because that is what `tracePath` sleeps
 * against, and both drivers must pace identically.
 *
 * `frequency` is samples per second (the driver passes 60).
 * `frequencyRandomizer` jitters each frame's timestamp so the sample interval
 * is not a metronome; a perfectly periodic mousemove stream is trivially
 * detectable.
 */
export function generate_trajectory(
  target_start: readonly number[],
  target_end: readonly number[],
  frequency: number = 60,
  frequencyRandomizer: number = 0.12,
): [Point[], number[]] {
  const start: Point = [Number(target_start[0]), Number(target_start[1])];
  const end: Point = [Number(target_end[0]), Number(target_end[1])];

  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const distance = Math.hypot(dx, dy);

  if (distance < 1e-6) return [[end], [0.0]];

  let durationMs = FITTS_A_MS + FITTS_B_MS * Math.log2(distance / WIDTH_PX + 1.0);
  durationMs *= uniform(0.85, 1.2);

  const steps = Math.max(2, Math.round((durationMs / 1000.0) * Math.max(1, frequency)));

  // A short hop is a straight ease with jitter — bowing a 15px move looks worse
  // than not bowing it.
  let c1: Point;
  let c2: Point;
  if (distance < SHORT_MOVE_PX) {
    c1 = start;
    c2 = end;
  } else {
    [c1, c2] = controlPoints(start, end, distance);
  }

  // Overshoot: on a long move the hand arrives past the target and corrects.
  // Modelled as a longer primary gesture to a point beyond the target, then a
  // short settle back — the settle is appended as extra samples below.
  const overshoot = distance >= OVERSHOOT_MIN_DISTANCE_PX && Math.random() < 0.55;
  let aim: Point = end;
  if (overshoot) {
    const over = uniform(0.01, 0.035) * distance;
    aim = [end[0] + (dx / distance) * over, end[1] + (dy / distance) * over];
  }

  const points: Point[] = [];
  const timings: number[] = [];
  let elapsed = 0.0;
  const perStep = durationMs / steps;

  for (let i = 0; i <= steps; i++) {
    const t = easeInOut(i / steps);
    let [px, py] = cubicBezier(start, c1, c2, aim, t);

    // Jitter scales with instantaneous speed: the faster the cursor is moving,
    // the less precisely a human tracks the intended path.
    const speed = Math.abs(easeInOut(Math.min(1.0, (i + 1) / steps)) - easeInOut(i / steps));
    const jitter = Math.min(1.6, 0.25 + speed * steps * 0.5);
    px += gauss(0.0, jitter * 0.5);
    py += gauss(0.0, jitter * 0.5);

    points.push([px, py]);
    timings.push(elapsed);
    elapsed += perStep * uniform(1.0 - frequencyRandomizer, 1.0 + frequencyRandomizer);
  }

  if (overshoot) {
    // Correction: a couple of small samples back onto the true target, at the
    // slower pace of a deliberate fine adjustment.
    const settleSteps = Math.floor(uniform(2, 5)); // 2..4 inclusive
    const origin = points[points.length - 1];
    for (let i = 1; i <= settleSteps; i++) {
      const t = i / settleSteps;
      points.push([
        origin[0] + (end[0] - origin[0]) * t + gauss(0.0, 0.35),
        origin[1] + (end[1] - origin[1]) * t + gauss(0.0, 0.35),
      ]);
      timings.push(elapsed);
      elapsed += uniform(14.0, 30.0);
    }
  }

  // Land exactly on the requested pixel. Everything above is texture; the final
  // sample must be the point the caller asked for or clicks drift off-target.
  points[points.length - 1] = end;
  return [points, timings];
}
