// Eight vendors expose no token, so the widget going away is the verdict, but only over two polls: a single poll can catch a re-deal's gap
// and call it a solve.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

function driver(detect: () => any) {
  const solver: any = new CaptchaKrakenSolver({
    postSolveOutcomeTimeoutMs: 1000,
    postSolveOutcomePollMs: 5,
  });
  const log = { detects: 0, solvedChecks: 0 };

  solver.human = { reset: async () => {}, at: [0, 0] };
  solver.detectCaptcha = async () => { log.detects += 1; return detect(); };
  solver.isCaptchaSolved = async () => { log.solvedChecks += 1; return false; };
  solver.isChallengeFreshlyRendered = async () => false;
  solver.hasRecaptchaUnderselectError = async () => false;
  solver.solveSingle = async () => ({ didInteract: true, tokenUsage: [] });
  return { solver, log };
}

test('two polls with the widget gone end the window', async () => {
  let calls = 0;
  const { solver, log } = driver(() => (++calls <= 1 ? {} : null));
  const result = await solver.solveImpl({});

  assert.equal(result.isSolved, true);

  assert.ok(log.detects <= 4,
    `the verdict window asked ${log.detects} times; two consecutive "the widget `
    + 'is gone" polls settle it, and waiting past that is time spent after the '
    + 'puzzle was already answered');
  assert.ok(log.solvedChecks <= 3,
    `the response-token check ran ${log.solvedChecks} times; it can never fire `
    + 'for a vendor that ships no token');
});

test('one gone poll is not a verdict', async () => {
  const seen = [{}, null, {}, {}, {}, {}, {}, {}];
  let i = 0;
  const { solver, log } = driver(() => (i < seen.length ? seen[i++] : {}));

  await assert.rejects(() => solver.solveImpl({}));

  assert.ok(log.detects > seen.length,
    'a lone gone poll among present ones must not short-circuit the window');
});
