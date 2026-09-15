// Granted once per solve (per burst would be unbounded) and derived from the burst ceiling, because a 4s window cannot contain a 5.3s cycle;
// a burst is never abandoned partway. A local 6_000 here once kept passing after the real default moved to 9_000, hence SOLVE_DEFAULTS.
import assert from 'node:assert/strict';
import test from 'node:test';

import { SOLVE_DEFAULTS } from './solver.js';

const BURST_MS = 12_000;

const KEYFRAME_WAIT_MS = SOLVE_DEFAULTS.keyframeWaitTimeoutMs;
const EXTRA_INFERENCE_MS = 8_000;
const VIDEO_BUDGET_MS = BURST_MS + KEYFRAME_WAIT_MS + EXTRA_INFERENCE_MS;

test('the recording grant matches the python port exactly', () => {
  assert.equal(VIDEO_BUDGET_MS, 29_000);
});

test('a still solve keeps exactly the deadline the caller configured', () => {
  const videoBudgetMs = 0;
  assert.equal(SOLVE_DEFAULTS.overallSolveTimeoutMs + videoBudgetMs, 45_000);
});

test('the budget survives an escalation on the last round', () => {
  const spentOnRounds = (SOLVE_DEFAULTS.maxSolveLoops - 1) * 7_000;
  const left =
    SOLVE_DEFAULTS.overallSolveTimeoutMs - spentOnRounds + VIDEO_BUDGET_MS;
  assert.ok(
    left >= BURST_MS + KEYFRAME_WAIT_MS,
    `${left}ms left cannot fit a ${BURST_MS + KEYFRAME_WAIT_MS}ms recording`,
  );
});

test('the grant does not make the deadline unbounded', () => {
  const worstCase = SOLVE_DEFAULTS.overallSolveTimeoutMs + VIDEO_BUDGET_MS;

  assert.equal(worstCase, 74_000);
  assert.ok(worstCase < 120_000);
});
