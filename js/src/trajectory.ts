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
 * `generate_swipe` is the SAME contract for a FINGER, and it is a different
 * model rather than the mouse model with different constants — see its
 * docstring.
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

// ── Touch ───────────────────────────────────────────────────────────────────
//
// Fitts's law holds for direct touch too, but with a slower intercept and a much
// wider effective target: a fingertip contact patch is ~9mm across, which is the
// 44pt / 48dp minimum both platform guidelines are built around. So the same
// distance takes longer AND is aimed less precisely than with a mouse.
const TOUCH_FITTS_A_MS = 160.0;
const TOUCH_FITTS_B_MS = 135.0;
const TOUCH_WIDTH_PX = 44.0;

// How flick-like the launch is. 0 would be the mouse's symmetric bell; 1 would
// be an instantaneous jump at t=0. See `easeTouch`.
const TOUCH_LAUNCH = 0.7;

// A finger drags across glass on a short wrist/thumb pivot, so it bows far less
// than a hand moving a mouse across a desk.
const TOUCH_BOW_CAP_PX = 40.0;

// The reported contact point wanders, because it is the CENTROID of a soft patch
// rolling under pressure rather than a rigid sensor. `TOUCH_WOBBLE_DECAY` is the
// AR(1) coefficient that makes that wander low-frequency: white per-sample noise
// would show up in a spectrum as nothing a finger produces.
const TOUCH_WOBBLE_PX = 0.55;
const TOUCH_WOBBLE_DECAY = 0.82;

/**
 * Asymmetric ease: a finger leaves fast and brakes late.
 *
 * A mouse hand is symmetric (`easeInOut` — accelerate, decelerate, equally). A
 * finger is not: the launch is a flick off the contact point and the arrival is
 * a brake. Blending the two rather than using a pure ease-out is what keeps the
 * launch from being an instantaneous jump at t=0, which no digitizer would ever
 * report.
 */
function easeTouch(t: number): number {
  return (1.0 - TOUCH_LAUNCH) * easeInOut(t) + TOUCH_LAUNCH * (1.0 - Math.pow(1.0 - t, 2.4));
}

/**
 * A FINGER travelling from `target_start` to `target_end`, same contract as
 * `generate_trajectory`: `[points, timings]`, timings cumulative from 0, last
 * point exactly `target_end`.
 *
 * A separate model rather than `generate_trajectory` with other constants,
 * because the three things that make the mouse output human are each WRONG
 * here:
 *
 *   - **No overshoot-and-correct.** That tell is a hand arriving past a target
 *     it cannot see under the cursor. A finger occludes its own target and
 *     commits; the correction, when there is one, is a second gesture.
 *   - **A different velocity profile.** `easeTouch`, not `easeInOut` — above.
 *   - **Different jitter.** The mouse model adds white noise scaled by speed (a
 *     hand tracking a path imprecisely). A digitizer instead reports a wandering
 *     centroid, which is low-frequency and roughly speed INDEPENDENT, so this is
 *     a smoothed random walk.
 *
 * `frequency` defaults to 90: digitizers sample at 120Hz+, but touchmove is
 * coalesced to the compositor, so more samples than this buys nothing and costs
 * a dispatch round-trip each.
 */
export function generate_swipe(
  target_start: readonly number[],
  target_end: readonly number[],
  frequency: number = 90,
  frequencyRandomizer: number = 0.1,
): [Point[], number[]] {
  const start: Point = [Number(target_start[0]), Number(target_start[1])];
  const end: Point = [Number(target_end[0]), Number(target_end[1])];

  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const distance = Math.hypot(dx, dy);

  if (distance < 1e-6) return [[end], [0.0]];

  let durationMs =
    TOUCH_FITTS_A_MS + TOUCH_FITTS_B_MS * Math.log2(distance / TOUCH_WIDTH_PX + 1.0);
  durationMs *= uniform(0.85, 1.2);

  const steps = Math.max(2, Math.round((durationMs / 1000.0) * Math.max(1, frequency)));

  let c1: Point;
  let c2: Point;
  if (distance < SHORT_MOVE_PX) {
    c1 = start;
    c2 = end;
  } else {
    [c1, c2] = controlPoints(start, end, distance);
    // Pull the mouse model's bow in: `controlPoints` is shared, and its arc is
    // sized for a hand crossing a desk.
    const shrink = Math.min(1.0, TOUCH_BOW_CAP_PX / Math.max(TOUCH_BOW_CAP_PX, distance * 0.16));
    c1 = [start[0] + (c1[0] - start[0]) * shrink, start[1] + (c1[1] - start[1]) * shrink];
    c2 = [start[0] + (c2[0] - start[0]) * shrink, start[1] + (c2[1] - start[1]) * shrink];
  }

  const points: Point[] = [];
  const timings: number[] = [];
  let elapsed = 0.0;
  const perStep = durationMs / steps;
  let wobX = 0.0;
  let wobY = 0.0;

  for (let i = 0; i <= steps; i++) {
    const t = easeTouch(i / steps);
    const [px, py] = cubicBezier(start, c1, c2, end, t);

    wobX = wobX * TOUCH_WOBBLE_DECAY + gauss(0.0, TOUCH_WOBBLE_PX);
    wobY = wobY * TOUCH_WOBBLE_DECAY + gauss(0.0, TOUCH_WOBBLE_PX);

    points.push([px + wobX, py + wobY]);
    timings.push(elapsed);
    elapsed += perStep * uniform(1.0 - frequencyRandomizer, 1.0 + frequencyRandomizer);
  }

  // Land exactly where the caller asked, same reason as the mouse model.
  points[points.length - 1] = end;
  return [points, timings];
}
