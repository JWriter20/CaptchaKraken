// An answer cache keyed on screenshot bytes replayed a failed answer on cycling boards: 81 loops, 12 model calls, 69 cache hits, 0 solves.
// A repeat is evidence, so it escalates to a recording; reCAPTCHA never is; the flag is per challenge.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

const ANSWER = { actions: [{ action: 'click', target_bounding_boxes: [[0.1, 0.1, 0.2, 0.2]] }], token_usage: [] };

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
  const { solver } = solverWithFixedAnswer();
  await solver.answerFor('same-picture', () => solver.askModel());
  await solver.answerFor('same-picture', () => solver.askModel());
  assert.equal(solver.repeatedAnswerSeen, true);

  solver.resetSolveState();

  assert.equal(solver.repeatedAnswerSeen, false);
  assert.equal(solver.shouldRetryAsAnimated('unknown'), false);
});
