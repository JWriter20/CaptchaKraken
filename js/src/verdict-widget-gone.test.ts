/**
 * The widget going away IS the success signal, for the vendors that have no other.
 *
 * After a submit the driver polls for a completion signal until
 * `postSolveOutcomeTimeoutMs`. `isCaptchaSolved` reads the hCaptcha and
 * reCAPTCHA anchors — a response token, a checked checkbox — and eight of the
 * vendors this client drives render into the host page with no such token at
 * all: GeeTest, Yidun, Tencent, Yandex, Lemin, Prosopo, MTCaptcha, BotDetect.
 *
 * So for those vendors this loop was waiting for something that cannot arrive,
 * and spent the WHOLE window on every round — after the puzzle had already been
 * answered, with the widget sitting there visibly solved. Measured in the
 * Python port on geetest_v4_slide before it got this fix: 5.2s of a 12.3s
 * solve. This port never got it, which is a large part of why it measured
 * slower than Python on exactly those families.
 *
 * "The widget is gone" is already the authority the moment this loop ends —
 * `detectCaptcha` is called immediately afterwards and a null there returns
 * solved. Checking it INSIDE the loop therefore reaches the same verdict
 * sooner and can reach no other one. It is confirmed over two consecutive
 * polls, because between rounds a vendor swaps the challenge frame out and a
 * single poll can catch that gap and call a re-deal a solve.
 *
 * The Python half is `page_solver.py`'s verdict loop, pinned by
 * CaptchaKrakenFinetune's tests/test_verdict_window_is_measured.py; per
 * CLAUDE.md 1c the two ports must behave the same.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

/**
 * A solver whose whole round is faked: one interacting round, then whatever
 * `detect` says. Counts how many times the verdict loop asked anything, which
 * is what tells a two-poll exit from a window burned to the end.
 */
function driver(detect: () => any) {
  const solver: any = new CaptchaKrakenSolver({
    postSolveOutcomeTimeoutMs: 1000,
    postSolveOutcomePollMs: 5,   // no browser here; the count is the measurement
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
  // The widget is there when the round starts and gone from the first verdict
  // poll on — an inline vendor that accepted the answer.
  let calls = 0;
  const { solver, log } = driver(() => (++calls <= 1 ? {} : null));
  const result = await solver.solveImpl({});

  assert.equal(result.isSolved, true);
  // 1 to find the widget + 2 verdict polls. A window burned to the end would
  // be ~200 at a 5ms poll.
  assert.ok(log.detects <= 4,
    `the verdict window asked ${log.detects} times; two consecutive "the widget `
    + 'is gone" polls settle it, and waiting past that is time spent after the '
    + 'puzzle was already answered');
  assert.ok(log.solvedChecks <= 3,
    `the response-token check ran ${log.solvedChecks} times; it can never fire `
    + 'for a vendor that ships no token');
});

test('one gone poll is not a verdict', async () => {
  // Between rounds hCaptcha swaps the challenge frame out, so a single poll can
  // land in the gap. Calling that a solve would end the solve on a re-deal.
  const seen = [{}, null, {}, {}, {}, {}, {}, {}];
  let i = 0;
  const { solver, log } = driver(() => (i < seen.length ? seen[i++] : {}));
  // The widget never goes for good, so this ends on the loop cap, not a verdict.
  await assert.rejects(() => solver.solveImpl({}));

  assert.ok(log.detects > seen.length,
    'a lone gone poll among present ones must not short-circuit the window');
});
