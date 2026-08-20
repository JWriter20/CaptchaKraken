/**
 * Regression: a board that cycles was solved as a still, forever.
 *
 * GeeTest's svg variant advances through screens of fresh glyphs and DWELLS on
 * each one — measured at p50 1.5s, p75 2.0s, max 2.7s per screen
 * (src/captchaCollection/sources.py). The settle probe declares a challenge
 * static after `settleFrames` (2) consecutive still polls at `settlePollMs`
 * (220ms), so roughly 440ms of stillness. A board that holds each screen for a
 * second and a half clears that bar trivially, so it was read as a still
 * picture, answered from whichever screen we happened to catch, and clicked
 * after that screen had gone.
 *
 * Then the answer cache made it permanent. Its key is the screenshot's bytes,
 * and its reasoning was "identical pixels mean the page has not changed, so
 * reuse the answer". On a cycling board the pixels come back around, so a later
 * round hashed the same and replayed the answer that had just failed — without
 * asking the model. Live, across 16 attempts: 81 solve loops, 12 model calls,
 * 69 cache hits, 0 solves.
 *
 * THE INVARIANT THE CACHE WAS MISSING. Every answer getSolution returns is
 * executed by the driver — there is no speculative call. So seeing the same
 * screenshot again cannot mean "nothing has changed, reuse it". It means the
 * answer we already tried changed nothing, which is the one situation where
 * replaying it is guaranteed to be wrong.
 *
 * So a repeat is not a saving, it is EVIDENCE: the still reading was wrong, and
 * the challenge is re-solved from a recorded burst instead — the same path
 * hCaptcha's animated challenges already take.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

const ANSWER = { actions: [{ action: 'click', target_bounding_boxes: [[0.1, 0.1, 0.2, 0.2]] }], token_usage: [] };

/** A solver whose model always answers the same thing, with nothing stubbed but the CLI. */
function solverWithFixedAnswer(config: Record<string, unknown> = {}): { solver: any; calls: () => number } {
  const solver: any = new CaptchaKrakenSolver(config);
  let calls = 0;
  solver.askModel = async () => {
    calls += 1;
    return ANSWER;
  };
  return { solver, calls: () => calls };
}

test('the same picture is not answered twice from cache', async () => {
  const { solver, calls } = solverWithFixedAnswer();

  const first = await solver.answerFor('same-picture', () => solver.askModel());
  const second = await solver.answerFor('same-picture', () => solver.askModel());

  assert.deepEqual(first.actions, ANSWER.actions);
  // The cache still saves the model call — that part was never the problem.
  assert.equal(calls(), 1, 'the second identical picture should not cost a model call');
  assert.deepEqual(second.actions, ANSWER.actions);
});

test('a repeated picture is recorded as evidence the still reading was wrong', async () => {
  const { solver } = solverWithFixedAnswer();

  assert.equal(solver.repeatedAnswerSeen, false, 'nothing has repeated yet');
  await solver.answerFor('same-picture', () => solver.askModel());
  assert.equal(solver.repeatedAnswerSeen, false, 'one answer is not a repeat');

  await solver.answerFor('same-picture', () => solver.askModel());
  assert.equal(solver.repeatedAnswerSeen, true, 'the second identical picture is the signal');
});

test('a different picture each round never trips the signal', async () => {
  // The overwhelmingly common case: every round shows a new board. Nothing here
  // may change for those, or this fix costs a burst recording on every solve.
  const { solver, calls } = solverWithFixedAnswer();

  for (const key of ['round-1', 'round-2', 'round-3', 'round-4']) {
    await solver.answerFor(key, () => solver.askModel());
  }

  assert.equal(solver.repeatedAnswerSeen, false);
  assert.equal(calls(), 4, 'four distinct boards are four model calls');
});

test('the signal escalates a non-reCAPTCHA challenge to the recorded path', async () => {
  const { solver } = solverWithFixedAnswer();

  assert.equal(solver.shouldRetryAsAnimated('unknown'), false);
  solver.repeatedAnswerSeen = true;
  assert.equal(solver.shouldRetryAsAnimated('unknown'), true);
  assert.equal(solver.shouldRetryAsAnimated('hcaptcha'), true);
});

test('reCAPTCHA is left alone', async () => {
  // reCAPTCHA's dynamic 3x3 REPLACES tiles in place and has its own multi-round
  // driver with its own fade gates. Its grids are never animated, so a burst
  // recording there would replace a path that works with one that cannot.
  const { solver } = solverWithFixedAnswer();
  solver.repeatedAnswerSeen = true;

  assert.equal(solver.shouldRetryAsAnimated('recaptcha'), false);
});

test('a caller who turned video solving off is not escalated into it', async () => {
  const { solver } = solverWithFixedAnswer({ videoSolveEnabled: false });
  solver.repeatedAnswerSeen = true;

  assert.equal(solver.shouldRetryAsAnimated('unknown'), false);
});

test('the signal does not survive into the next solve', async () => {
  // It is a fact about one challenge, not about the solver. A page whose first
  // captcha cycled must not make the next one record a burst it does not need.
  const { solver } = solverWithFixedAnswer();
  await solver.answerFor('same-picture', () => solver.askModel());
  await solver.answerFor('same-picture', () => solver.askModel());
  assert.equal(solver.repeatedAnswerSeen, true);

  solver.resetSolveState();

  assert.equal(solver.repeatedAnswerSeen, false);
  assert.equal(solver.shouldRetryAsAnimated('unknown'), false);
});
