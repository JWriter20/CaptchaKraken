/**
 * Human-like mouse trajectories for the page driver.
 *
 * The TypeScript side of `python/src/captchakraken/trajectory.py`; the two are
 * one implementation in two languages, pinned by `test_trajectory_parity.py`.
 *
 * THE MOUSE PATH IS NOT OURS AND SHOULD NOT BE. It was a Bezier arc with a
 * Fitts's-law duration, an ease-in-out velocity profile, speed-scaled jitter and
 * an overshoot-and-correct — every one of those a MODEL of what a hand does,
 * tuned by hand, and each one a thing a detector can look for precisely because
 * it is a closed form. `cursory-js` does not model anything: it finds the
 * closest match in a database of thousands of trajectories recorded from real
 * people, morphs it onto the requested endpoints, and adds noise. The realism is
 * measured rather than asserted, and the failure mode is a bad recording rather
 * than a recognisable curve.
 *
 * It also settles the dual-port problem for good. The two drivers used to carry
 * one algorithm written twice, pinned by a statistical parity test, because that
 * is the best two independent implementations can do. `cursory-js` is a port of
 * Python `cursory` rather than a rewrite and agrees BIT FOR BIT on a seed — both
 * reduce to the same numpy PCG64 stream — so the two drivers now share a mouse
 * rather than resembling each other. Measured: seed 42 over (120, 80) ->
 * (940, 560) gives 48 identical points and identical timings in both.
 *
 * `generate_swipe` below is OURS and stays ours: it is the same contract for a
 * FINGER, and Cursory records mice. A finger is not a slower mouse — different
 * velocity profile, different bow, a contact patch that wanders — so it is a
 * different model rather than this one with other constants. See its docstring.
 *
 * Camoufox's own `humanize` is a DIFFERENT, browser-level mechanism that
 * re-humanises every `mouse.move()` it is handed. Running both composes them —
 * measured 82.1s vs 13.4s on one a GeeTest v4 slider solve, because each of the 60
 * points below became its own humanised sub-trajectory. Drive with humanize off.
 */

import { generateTrajectory } from 'cursory-js';

export type Point = [number, number];





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
 * Returns `[points, timings]`. `timings` is CUMULATIVE milliseconds from the
 * gesture start — not per-step deltas — because that is what `tracePath` sleeps
 * against, and both drivers must pace identically.
 *
 * `frequency` is samples per second (the driver passes 60).
 * `frequencyRandomizer` jitters each frame's timestamp so the sample interval
 * is not a metronome; a perfectly periodic mousemove stream is trivially
 * detectable.
 */
// How far a path may run past its endpoint, along the travel axis, before it is
// redrawn. Small enough to keep "this gesture does not overshoot" true, large
// enough not to reject a path for a rounding artefact.
const OVERSHOOT_TOL_PX = 2.0;

// How many times to redraw before taking the best draw seen. Never fails: a
// gesture that cannot find a clean path still moves.
const OVERSHOOT_REDRAWS = 6;

/** How far past `end` the path travels, projected on the travel axis. */
function overshootOf(points: Point[], start: Point, end: Point): number {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const length = Math.hypot(dx, dy);
  if (length === 0) return 0;
  const ux = dx / length;
  const uy = dy / length;
  let furthest = -Infinity;
  for (const [x, y] of points) {
    const along = (x - start[0]) * ux + (y - start[1]) * uy;
    if (along > furthest) furthest = along;
  }
  return furthest - length;
}

/**
 * One Cursory trajectory, redrawn until it does not run past its endpoint.
 *
 * REDRAWN, NOT RESHAPED. Cursory's output is a recording of a real movement
 * morphed onto these endpoints, and the whole reason to use it is that nobody
 * drew the curve. Clamping or straightening would put a hand-tuned step back in
 * the middle of a recording; drawing a different one keeps every gesture real.
 *
 * WHY ANY OF THEM RUN PAST. A recording carries whatever excursion the person
 * made relative to their OWN endpoints, and morphing scales that onto ours — so
 * a wandering recording becomes a short movement that swings well past the
 * target and returns. Measured over 400 draws per distance: 6-25% run more than
 * 2px past, with a tail reaching 200px past a 150px movement. For a pointer
 * travelling to a click that is realistic and free; for a DRAG it is not,
 * because the pointer is carrying something.
 *
 * `directness` cannot fix it — it selects among recordings rather than
 * straightening one, so overshoot bottoms out near 12% at 0.65 (the default
 * here) and rises again toward 1.0.
 *
 * A SEED STILL MEANS ONE TRAJECTORY: attempts are derived from it, so a seeded
 * call stays reproducible and the cross-port fixture holds.
 */
function draw(
  start: Point, end: Point, frequency: number, frequencyRandomizer: number,
  seed: number | bigint | undefined, directness: number, avoidOvershoot: boolean,
): [Point[], number[]] {
  let best: [Point[], number[]] | null = null;
  let bestOver = Infinity;
  const attempts = avoidOvershoot ? OVERSHOOT_REDRAWS : 1;
  for (let i = 0; i < attempts; i += 1) {
    const attemptSeed = seed === undefined
      ? undefined
      : (typeof seed === 'bigint' ? seed + BigInt(i) : seed + i);
    const { points, timings } = generateTrajectory(start, end, {
      frequency, frequencyRandomizer, seed: attemptSeed, directness,
    });
    const pts = points.map((q) => [Number(q[0]), Number(q[1])] as Point);
    const ts = timings.map(Number);
    if (!avoidOvershoot) return [pts, ts];
    const over = overshootOf(pts, start, end);
    if (over <= OVERSHOOT_TOL_PX) return [pts, ts];
    if (over < bestOver) { best = [pts, ts]; bestOver = over; }
  }
  return best as [Point[], number[]];
}


export function generate_trajectory(
  targetStart: readonly number[],
  targetEnd: readonly number[],
  frequency = 60,
  frequencyRandomizer = 1.0,
  seed?: number | bigint,
  directness = 0.65,
  avoidOvershoot = false,
): [Point[], number[]] {
  // `frequencyRandomizer` is the largest jitter applied to each sample time, IN
  // MILLISECONDS — it was a fraction while this was a curve, and the units
  // changed with the model. `seed` makes a movement reproducible; production
  // leaves it undefined, which draws fresh entropy per call.
  //
  // A thin delegation, and deliberately thin: the arguments are Cursory's own,
  // so there is no second set of knobs here to drift from the ones that do
  // something.
  return draw(
    [Number(targetStart[0]), Number(targetStart[1])],
    [Number(targetEnd[0]), Number(targetEnd[1])],
    frequency, frequencyRandomizer, seed, directness, avoidOvershoot);
}

// ── Touch ───────────────────────────────────────────────────────────────────
//
// The PATH is Cursory's, the same as the mouse. What stays here is the one thing
// a trajectory library cannot know, because it is not about the path at all: a
// touchscreen reports the CENTROID OF A CONTACT PATCH, and that centroid wanders
// under finger pressure independently of where the finger is going. A swipe
// whose samples sit exactly on a generated path is a swipe no digitizer produced.
//
// Everything else that used to live here — a Fitts's-law duration, an asymmetric
// ease, a bowed Bezier, a short-move threshold — was a MODEL of finger motion
// built from platform tap-target guidelines rather than from measurement, which
// is the same kind of hand-tuned closed form the mouse model was replaced for.
//
// MEASURED 2026-09-14 over captcha-sized drags, bow from the straight line at
// 90 Hz, against the model that used to be here:
//
//     80px    ours 6.0px    cursory 5.8px     equivalent
//     150px   ours 11.4px   cursory 23.7px    cursory arcs about twice as far
//     250px   ours 17.9px   cursory 24.3px
//
// So this is not a free swap and it is recorded as such: a finger on a wrist
// pivot plausibly bows less than a hand moving a mouse across a desk, and
// Cursory's `directness` does not tune it — it selects among RECORDINGS, so it
// does nothing at 150px and overshoots at 250px. What settles it is that our
// numbers were asserted and Cursory's are measured from real people; a
// hand-tuned constant is a fingerprint surface whoever wrote it.

// The contact patch's centroid wander. `TOUCH_WOBBLE_DECAY` is an AR(1)
// coefficient, which is what makes the wander LOW-FREQUENCY: white per-sample
// noise would show up in a spectrum as nothing a finger produces.
const TOUCH_WOBBLE_PX = 0.55;
const TOUCH_WOBBLE_DECAY = 0.82;

/**
 * A FINGER travelling from `targetStart` to `targetEnd`.
 *
 * Same contract as `generate_trajectory`: `[points, timings]`, timings
 * cumulative from 0, last point exactly `targetEnd`.
 *
 * Cursory's path, sampled faster — a digitizer reports at a higher and steadier
 * rate than a mouse, so the default frequency is 90 rather than 60 — with the
 * contact wobble laid over it. See the section comment above for what was
 * removed and what the swap measurably costs.
 *
 * ONLY EVER CALLED WHILE THE FINGER IS DOWN. A tap generates no path at all:
 * `MobileHumanizer.move` records the position and dispatches nothing when there
 * is no contact, because there is no hover on a touchscreen.
 */
// A FINGER PREFERS THE EFFICIENT RECORDINGS. Cursory's `directness` biases
// selection toward shorter, straighter movements, and for touch that is worth
// spending: a drag is the one gesture a person is watching happen, and a
// wandering recording morphed onto a 150px slider reads as sluggish.
//
// MEASURED 2026-09-14 over 80/150/250px drags at 90 Hz, 600 draws per setting:
//
//     directness   duration p50   p90    bow p50   redraws
//     0.65 (mouse)     519 ms    729 ms   11.7px    10.7%
//     0.85 (here)      400 ms    729 ms    9.9px    10.8%
//     0.90             400 ms    729 ms    9.8px    15.0%
//
// 0.85 takes the whole of the speed-up — 23% off the median, and 40% at 150px,
// which is where a slider drag lives — while the bow gets slightly TIGHTER,
// which is the direction a finger on a wrist pivot should go. 0.9 buys no more
// time and costs half again as many redraws.
//
// The mouse stays at Cursory's own 0.65. Its overshoot floor is lowest there,
// and a pointer merely travelling to a click has no reason to hurry.
const SWIPE_DIRECTNESS = 0.85;

export function generate_swipe(
  targetStart: readonly number[],
  targetEnd: readonly number[],
  frequency = 90,
  frequencyRandomizer = 1.0,
  seed?: number | bigint,
  directness = SWIPE_DIRECTNESS,
): [Point[], number[]] {
  // Always: `generate_swipe` is only ever called while the finger is down, so
  // every path it produces is a drag carrying something.
  const [points, timings] = draw(
    [Number(targetStart[0]), Number(targetStart[1])],
    [Number(targetEnd[0]), Number(targetEnd[1])],
    frequency, frequencyRandomizer, seed, directness, true);
  const out: Point[] = [];
  let wobX = 0.0;
  let wobY = 0.0;
  const last = points.length - 1;
  for (let i = 0; i < points.length; i += 1) {
    const [x, y] = points[i];
    if (i === 0 || i === last) {
      // The ends are where the touch lands and lifts; a wobble there moves the
      // gesture rather than texturing it.
      out.push([Number(x), Number(y)]);
      continue;
    }
    wobX = wobX * TOUCH_WOBBLE_DECAY + gauss(0.0, TOUCH_WOBBLE_PX);
    wobY = wobY * TOUCH_WOBBLE_DECAY + gauss(0.0, TOUCH_WOBBLE_PX);
    out.push([Number(x) + wobX, Number(y) + wobY]);
  }
  return [out, timings.map(Number)];
}
