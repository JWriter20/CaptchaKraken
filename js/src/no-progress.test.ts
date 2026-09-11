/**
 * A solve that repeats itself must stop, not run out the clock.
 *
 * At temperature 0 the model is a function of the picture, and every answer this
 * driver produces is EXECUTED. So the same answer arriving twice means the
 * previous one already ran and the page is still asking the same question.
 * Performing it again cannot do better — it just spends a round.
 *
 * MEASURED in the Python port, a reCAPTCHA 4x4 grid, the fixture,
 * adapter captcha-v12:
 *
 *     loop 1  [2,6,7,9,10]
 *     loop 2  [2,6,7,10]
 *     loop 3  [2,6,7,10]      <- and identically for loops 4..10
 *     -> "captcha still detected after 10 solve loops", 66.1s, 39.0s of it waiting
 *
 * Stopping on the second repeat ends that solve at round 4 instead of round 10.
 *
 * This file is the JS half of that fix. Both ports drive the same fixtures under
 * Tier 3 and CLAUDE.md 1c requires them to behave the same, so the rule is
 * pinned twice — here and in the training repo's
 * tests/test_no_progress_bailout.py, which carries the full measurement.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver, SOLVE_DEFAULTS } from './solver';

type Box = [number, number, number, number];

function click(...boxes: Box[]) {
  return [{ action: 'click', target_bounding_boxes: boxes.map((b) => [...b]) }];
}

/** The real sequence, from the run in the docstring. */
const MEASURED_4X4 = [
  click([0.1, 0.1, 0.2, 0.2], [0.3, 0.1, 0.4, 0.2], [0.5, 0.5, 0.6, 0.6]),
  ...Array.from({ length: 9 }, () => click([0.1, 0.1, 0.2, 0.2], [0.3, 0.1, 0.4, 0.2])),
];

const solver = (config: Record<string, unknown> = {}): any =>
  new CaptchaKrakenSolver(config);

test('the measured 4x4 sequence stops at round four', () => {
  const s = solver();
  let stoppedAt: number | null = null;
  for (let i = 0; i < MEASURED_4X4.length; i++) {
    s.noteAnswer(MEASURED_4X4[i], null);
    if (s.noProgressRounds >= (s.config.maxNoProgressRounds ?? 2)) {
      stoppedAt = i + 1;
      break;
    }
  }
  assert.equal(stoppedAt, 4, 'rounds 2,3,4 are identical, so round 4 is where it gives up');
});

test('a changing answer never trips it', () => {
  const s = solver();
  for (let i = 0; i < 10; i++) {
    s.noteAnswer(click([0.1 * i, 0.1, 0.2, 0.2]), null);
    assert.equal(s.noProgressRounds, 0, 'a solve making progress must not be cut off');
  }
});

test('the retry mode is part of the answer', () => {
  // The missed-tiles retry re-asks about the same board on purpose, so its
  // answer legitimately overlaps the one before it. Counting that as a repeat
  // would abandon the single path built to recover from an under-selection.
  const s = solver();
  s.noteAnswer(click([0.1, 0.1, 0.2, 0.2]), null);
  s.noteAnswer(click([0.1, 0.1, 0.2, 0.2]), 'missed-tiles');
  assert.equal(s.noProgressRounds, 0);
});

test('the first repeat escalates to a recording before the second abandons', () => {
  // A board that reads the same every round may be CYCLING, not stuck, and
  // recording it is the one recovery that can still work.
  const s = solver();
  s.noteAnswer(click([0.1, 0.1, 0.2, 0.2]), null);
  assert.equal(s.repeatedAnswerSeen, false);
  s.noteAnswer(click([0.1, 0.1, 0.2, 0.2]), null);
  assert.equal(s.repeatedAnswerSeen, true, 'the first repeat should arm the recording path');
});

test('coordinates are compared rounded, not exactly', () => {
  // The same tile chosen twice can differ in the last float digit after the
  // normalise/clamp round-trip. A repeat that reads as "different" costs a round.
  const s = solver();
  s.noteAnswer(click([0.1, 0.1, 0.2, 0.2]), null);
  s.noteAnswer(click([0.10000001, 0.1, 0.2, 0.2]), null);
  assert.equal(s.noProgressRounds, 1);
});

test('an unreadable answer is not a repeat', () => {
  // A signature is an optimisation; it must never be why a solve is dropped.
  const s = solver();
  const hostile = { get action(): string { throw new Error('nope'); } };
  for (let i = 0; i < 5; i++) s.noteAnswer([hostile], null);
  assert.equal(s.noProgressRounds, 0);
});

test('the budget fits the loop count', () => {
  // The cap is a BACKSTOP, so the loop count must be what actually bounds a
  // solve. Six rounds at the ~4-7s a round costs is 24-42s, inside the 45s cap.
  // If the loop count ever exceeds what the cap can hold, the timeout goes back
  // to being the thing that ends solves — the state this work removed.
  const { maxSolveLoops, overallSolveTimeoutMs } = SOLVE_DEFAULTS;
  assert.ok(
    maxSolveLoops * 7_000 <= overallSolveTimeoutMs,
    `${maxSolveLoops} rounds x 7000ms exceeds the ${overallSolveTimeoutMs}ms cap`,
  );
});

/*
 * A CACHED ANIMATED ANSWER MUST NOT OUTLIVE ITS REFUSAL.
 *
 * `animatedPlan` holds one burst and one inference for as long as a board is on
 * screen, which is right while the answer is merely untested: the frames do not
 * change, so re-recording buys nothing. Once the widget has refused the answer
 * it is wrong, because re-submitting identical coordinates cannot succeed — and
 * the escalation above raises the SAMPLE, which never reaches the wire while a
 * cached response stands in front of it.
 *
 * The rule is therefore two-part and both halves matter: drop the answer, keep
 * the frames. Dropping both would spend another `videoBurstMaxMs` filming
 * screens already in hand.
 *
 * Mirrors `_invalidate_animated_answer` in the Python port. Both ports drive the
 * same fixtures under Tier 3 and must behave identically.
 */
test('a repeat drops the cached animated answer but keeps the frames', () => {
  const s = solver();
  s.animatedPlan = { burstDir: '/tmp/burst-abc', response: { actions: [] } };

  s.noteAnswer(click([0.1, 0.1, 0.2, 0.2]), null);
  s.noteAnswer(click([0.1, 0.1, 0.2, 0.2]), null);

  assert.equal(s.animatedPlan.response, null, 'the refused answer must not be re-served');
  assert.equal(s.animatedPlan.burstDir, '/tmp/burst-abc', 'the frames are still good');
});

test('an answer that is still making progress keeps its recording', () => {
  // Invalidating on every round would re-ask once per round on a board that is
  // being solved correctly, which is one inference per round of pure cost.
  const s = solver();
  const plan = { burstDir: '/tmp/burst-abc', response: { actions: [] } };
  s.animatedPlan = plan;

  s.noteAnswer(click([0.1, 0.1, 0.2, 0.2]), null);
  s.noteAnswer(click([0.5, 0.5, 0.6, 0.6]), null);

  assert.equal(s.animatedPlan.response, plan.response, 'a changing answer is not a refusal');
});

test('invalidating twice is not an error', () => {
  // The no-progress path can fire again before the next round reaches a
  // request; the second call must be a no-op rather than clearing the frames.
  const s = solver();
  s.animatedPlan = { burstDir: '/tmp/burst-abc', response: { actions: [] } };
  s.invalidateAnimatedAnswer();
  s.invalidateAnimatedAnswer();

  assert.equal(s.animatedPlan.response, null);
  assert.equal(s.animatedPlan.burstDir, '/tmp/burst-abc');
});
