// The missed-tiles retry legitimately overlaps the answer before it, coordinates are compared rounded, and the loop cap is the real
// bound: six rounds at 4-7s each is 24-42s, inside the 45s cap.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver, SOLVE_DEFAULTS } from './solver';

type Box = [number, number, number, number];

function click(...boxes: Box[]) {
  return [{ action: 'click', target_bounding_boxes: boxes.map((b) => [...b]) }];
}

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
  const s = solver();
  s.noteAnswer(click([0.1, 0.1, 0.2, 0.2]), null);
  s.noteAnswer(click([0.1, 0.1, 0.2, 0.2]), 'missed-tiles');
  assert.equal(s.noProgressRounds, 0);
});

test('the first repeat escalates to a recording before the second abandons', () => {
  const s = solver();
  s.noteAnswer(click([0.1, 0.1, 0.2, 0.2]), null);
  assert.equal(s.repeatedAnswerSeen, false);
  s.noteAnswer(click([0.1, 0.1, 0.2, 0.2]), null);
  assert.equal(s.repeatedAnswerSeen, true, 'the first repeat should arm the recording path');
});

test('coordinates are compared rounded, not exactly', () => {
  const s = solver();
  s.noteAnswer(click([0.1, 0.1, 0.2, 0.2]), null);
  s.noteAnswer(click([0.10000001, 0.1, 0.2, 0.2]), null);
  assert.equal(s.noProgressRounds, 1);
});

test('an unreadable answer is not a repeat', () => {
  const s = solver();
  const hostile = { get action(): string { throw new Error('nope'); } };
  for (let i = 0; i < 5; i++) s.noteAnswer([hostile], null);
  assert.equal(s.noProgressRounds, 0);
});

test('the budget fits the loop count', () => {
  const { maxSolveLoops, overallSolveTimeoutMs } = SOLVE_DEFAULTS;
  assert.ok(
    maxSolveLoops * 7_000 <= overallSolveTimeoutMs,
    `${maxSolveLoops} rounds x 7000ms exceeds the ${overallSolveTimeoutMs}ms cap`,
  );
});

// Drop the ANSWER, keep the FRAMES: re-submitting coordinates the widget refused cannot succeed, and the
// raised sample never reaches the wire behind a cached response; re-recording screens already in hand is
// another `videoBurstMaxMs` for nothing. Mirrors `_invalidate_animated_answer` in the Python port.
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

// A REFUSED ANSWER IS NO PROGRESS EVEN WHEN THE WORDS CHANGE. Once the film runs for the whole solve, each
// round re-slices a longer clip and the model answers differently, so the signature fence above never sees a
// repeat — and an animated board ran all six loops however hopeless it was. Measured on the gate: video board
// windows went 41 to 67 for the same 102 pairs and the same ~240 attempts, which is rounds, not work.
test('an animated board that keeps being refused gives up before the loop cap', () => {
  const s = solver();
  const asks: number[] = [];
  let stoppedAt: number | null = null;

  for (let round = 1; round <= 6; round++) {
    if (s.refusedAnimatedAsks >= (s.config.maxNoProgressRounds ?? 2)) { stoppedAt = round; break; }
    if (s.animatedPlan?.response) {
      // The reuse round: the widget already refused this, so `noteAnswer` drops it and nothing is pressed.
      s.noteAnswer(s.animatedPlan.response.actions, null);
    } else {
      // The ask round: a longer film, so a different answer every time.
      const answer = click([0.1 * round, 0.1, 0.2, 0.2]);
      asks.push(round);
      s.animatedPlan = { burstDir: `/tmp/ck_slice_${round}`, response: { actions: answer, token_usage: [] } };
      s.noteAnswer(answer, null);
    }
  }

  // The reuse round repeats the answer once and the next ask resets it, so the signature fence oscillates
  // between 0 and 1 and never reaches its bound. That is what left the loop cap as the only stop.
  assert.ok(s.noProgressRounds < (s.config.maxNoProgressRounds ?? 2),
    `the signature fence reached ${s.noProgressRounds}; this test is meant to be the case it cannot see`);
  assert.equal(stoppedAt, 5, 'two refused recordings is the bound, so round five is where it gives up');
  assert.deepEqual(asks, [1, 3], 'the board was asked about twice, not six times');
});
