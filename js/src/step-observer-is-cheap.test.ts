/**
 * Watching a solve must not slow it down.
 *
 * `onStep` hands the caller a screenshot at each stage. That snapshot was taken
 * with `elementScreenshotTimeoutMs` — the budget sized for the picture the
 * MODEL reads, 8s — and with `animations: 'disabled'`, which makes Playwright
 * wait for the element to STOP MOVING before it will take it. A captcha widget
 * that is still animating never stops, so the wait ran to the full budget, per
 * step.
 *
 * MEASURED on mtcaptcha_text, fixture seed 20260730, with the timestamps
 * printed from inside the round:
 *
 *     detect-done       59ms
 *     screenshot-done  818ms
 *     inference-done  2554ms
 *     answerBox         12ms
 *     moveAndClick     315ms
 *     typeText         465ms
 *     actions-done   11351ms      <- 8s of nothing, after the code was typed
 *
 * Eight seconds photographing a text box for a trace, on a solve whose actual
 * work was three and a half. An observer must never cost more than the action
 * it observes, and a missed frame in a trace costs nothing.
 *
 * The Python port has no onStep, so this was also a difference between the two
 * ports that only showed up as latency (CLAUDE.md 1c).
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

/** The longest an observability snapshot may block a solve. */
const MAX_STEP_SNAPSHOT_MS = 3000;

function seen(config: Record<string, any> = {}) {
  const shots: any[] = [];
  const solver: any = new CaptchaKrakenSolver({ onStep: async () => {}, ...config });
  const element = { screenshot: async (opts: any) => { shots.push(opts); } };
  return { solver, element, shots };
}

test('the step snapshot has its own short budget', async () => {
  const { solver, element, shots } = seen({ elementScreenshotTimeoutMs: 8000 });
  await solver.emitStep(element, 'initial', 'x', 'unknown', 'challenge', 1);

  assert.equal(shots.length, 1);
  assert.ok(shots[0].timeout <= MAX_STEP_SNAPSHOT_MS,
    `an onStep snapshot may block the solve for ${shots[0].timeout}ms. That is `
    + "the model's screenshot budget, and this picture is a trace — with "
    + 'animations disabled it waits for the widget to stop moving, which on an '
    + 'animated challenge means the whole budget, on every step.');
});

test('the caller can still size it', async () => {
  const { solver, element, shots } = seen({ stepScreenshotTimeoutMs: 500 });
  await solver.emitStep(element, 'initial', 'x', 'unknown', 'challenge', 1);
  assert.equal(shots[0].timeout, 500);
});

test('no observer, no snapshot', async () => {
  // The whole cost is skipped for the ordinary caller, and must stay skipped.
  const solver: any = new CaptchaKrakenSolver({});
  const shots: any[] = [];
  await solver.emitStep({ screenshot: async (o: any) => { shots.push(o); } },
    'initial', 'x', 'unknown', 'challenge', 1);
  assert.equal(shots.length, 0);
});
