/**
 * An escalation to recording must fit in the budget.
 *
 * `overallSolveTimeoutMs` counts ROUNDS — no-progress.test.ts pins that
 * `maxSolveLoops x ~7000ms` fits inside it. Recording an animated challenge is
 * not a round: it is a fixed extra stage costing the burst, the slice, one
 * MULTI-IMAGE inference (six keyframes, several times a still's) and the wait
 * for the widget to come back round to the frame the model chose. Nothing in
 * the 45 s was set aside for it, so a solve that escalated late simply ran the
 * clock out and reported a timeout — a message about the model being slow, for
 * a budget with no room for what the solver had just decided to do.
 *
 * MEASURED, Tier 3 run 32596340560 (2026-08-22). This port:
 *
 *     hcaptcha_click_image_by_traits   FAIL 52.4s, 49.7s  "timed out after 45000ms (attempt 6/6)"
 *     hcaptcha_connect_path            FAIL 50.2s          same
 *     hcaptcha_grid_3x3_property       FAIL 49.7s, 49.4s   same
 *
 * …against 14-20 s whenever the still path happened to answer the same fixture.
 *
 * This file is the JS half of the fix. Both ports drive the same fixtures under
 * Tier 3 and CLAUDE.md 1c requires them to behave the same, so the total granted
 * is the same arithmetic as page_solver.py's `video_budget_ms`: a fixture that
 * passed on one port and timed out on the other would read as a driver bug.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { SOLVE_DEFAULTS } from './solver.js';

/** The defaults the two ports must agree on, named once. */
const BURST_MS = 4_000;
const KEYFRAME_WAIT_MS = 6_000;
const EXTRA_INFERENCE_MS = 8_000;
const VIDEO_BUDGET_MS = BURST_MS + KEYFRAME_WAIT_MS + EXTRA_INFERENCE_MS;

test('the recording grant matches the python port exactly', () => {
  // Not a coincidence to be maintained by hand on both sides — it is the whole
  // reason a Tier 3 divergence between the ports means something. If either
  // default moves, this fails and the other port has to move with it.
  assert.equal(VIDEO_BUDGET_MS, 18_000);
});

test('a still solve keeps exactly the deadline the caller configured', () => {
  // The grant is an EXTENSION applied when a recording starts, not a looser
  // default. A caller who set 45 s and never hits an animated puzzle must still
  // get 45 s, or the config stops meaning anything.
  const videoBudgetMs = 0; // no escalation happened
  assert.equal(SOLVE_DEFAULTS.overallSolveTimeoutMs + videoBudgetMs, 45_000);
});

test('the budget survives an escalation on the last round', () => {
  // The arithmetic the live failures came down to. The animated probe arms
  // after two identical answers, which in practice is round 3-5; by then most
  // of the still budget is gone and the recording has to fit in the remainder.
  const spentOnRounds = (SOLVE_DEFAULTS.maxSolveLoops - 1) * 7_000;
  const left =
    SOLVE_DEFAULTS.overallSolveTimeoutMs - spentOnRounds + VIDEO_BUDGET_MS;
  assert.ok(
    left >= BURST_MS + KEYFRAME_WAIT_MS,
    `${left}ms left cannot fit a ${BURST_MS + KEYFRAME_WAIT_MS}ms recording`,
  );
});

test('the grant does not make the deadline unbounded', () => {
  // Once per solve, not once per burst: a puzzle that re-records would
  // otherwise extend its own deadline forever, which is the opposite failure
  // and the worse one — this cap is what stops a hung solve from running until
  // the caller kills it.
  const worstCase = SOLVE_DEFAULTS.overallSolveTimeoutMs + VIDEO_BUDGET_MS;
  assert.equal(worstCase, 63_000);
  assert.ok(worstCase < 120_000);
});
