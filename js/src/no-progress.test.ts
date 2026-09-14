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
