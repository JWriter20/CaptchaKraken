/**
 * A still board is not a loaded board.
 *
 * Every gate in front of the inference screenshot asks whether the widget has
 * stopped CHANGING. The blank panel a vendor shows while it rebuilds the board
 * answers that with a confident yes — it is the stillest the widget ever is —
 * so the solver photographs the hole and asks the model to solve it. The model
 * answers the only way it can, with the middle of the image.
 *
 * MEASURED on gt4.geetest.com's slide demo, 2026-09-12, ten live attempts with
 * every request banked through a proxy in front of vLLM:
 *
 *     52 requests, 6 of them a panel with no puzzle in it
 *     every one of the 6 came back `to:[480,310]`-ish — dead centre
 *     attempt 1  the driver EXECUTED one: a drag to 48.5%, round burnt
 *     attempt 2  ended "solver performed no interactions" on a blank board
 *
 * The pairing that catches this today is `waitForHcaptchaChallengeImages`,
 * which asks the DOM and only knows hCaptcha. `waitForBoardPainted` asks the
 * picture, so it holds for GeeTest and everything else.
 *
 * The Python half is python/tests/test_a_blank_board_is_not_photographed.py;
 * both ports drive the same fixtures under Tier 3 and must answer alike.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { resolve } from 'node:path';

import { CaptchaKrakenSolver } from './solver';

/** An element whose screenshot always succeeds, writing a byte to the path. */
const el = () => ({
  async screenshot({ path }: { path: string }) { fs.writeFileSync(path, 'x'); },
});

/** A solver whose CV tool returns the given verdicts in order, then the last. */
function solverSeeing(verdicts: Array<boolean | null>, config: Record<string, unknown> = {}) {
  const s: any = new CaptchaKrakenSolver({ boardPaintPollMs: 1, boardPaintTimeoutMs: 60, ...config });
  let i = 0;
  s.calls = 0;
  s.runCvTool = async () => {
    s.calls++;
    const v = verdicts[Math.min(i++, verdicts.length - 1)];
    return { painted: v, texture: v ? 0.5 : 0.0, floor: 0.015 };
  };
  return s;
}

test('a painted board is photographed at once, with no wait', async () => {
  const s = solverSeeing([true]);
  const { verdict, waitedMs } = await s.waitForBoardPainted(el());
  assert.equal(verdict, 'painted');
  assert.equal(s.calls, 1);
  assert.ok(waitedMs < 60, `should not have waited, waited ${waitedMs}ms`);
});

test('a blank board is polled until it paints, and is not photographed blank', async () => {
  const s = solverSeeing([false, false, false, true]);
  const { verdict } = await s.waitForBoardPainted(el());
  assert.equal(verdict, 'painted');
  assert.equal(s.calls, 4, 'should have kept looking until there was a puzzle');
});

test('a board that never paints falls through rather than stalling the solve', async () => {
  // The whole point of the budget: a gate that can refuse to ever take a
  // picture turns one wasted round into a dead solve.
  const s = solverSeeing([false]);
  const started = Date.now();
  const { verdict } = await s.waitForBoardPainted(el());
  assert.equal(verdict, 'blank');
  assert.ok(Date.now() - started < 2000, 'must be bounded by boardPaintTimeoutMs');
});

test('an unreadable image is not treated as a blank one', async () => {
  // `painted: null` means the CV tool could not open the file. Looping on it
  // would spend the whole budget every round on a box that never opens.
  const s = solverSeeing([null]);
  const { verdict } = await s.waitForBoardPainted(el());
  assert.equal(verdict, 'unknown');
  assert.equal(s.calls, 1);
});

test('a screenshot that throws is a skipped poll, not a verdict', async () => {
  const s = solverSeeing([true]);
  let first = true;
  const flaky = {
    async screenshot({ path }: { path: string }) {
      if (first) { first = false; throw new Error('element is detaching'); }
      fs.writeFileSync(path, 'x');
    },
  };
  const { verdict } = await s.waitForBoardPainted(flaky);
  assert.equal(verdict, 'painted');
});

test('the freshness re-solve waits for paint before it re-photographs', async () => {
  /*
   * The path that most needs the gate, and the one it was missing.
   *
   * `solveFrameFreshnessGuarded` re-queries when the frame changed during
   * inference — which is exactly the moment a vendor that rebuilds its panel
   * between rounds is showing the blank. MEASURED 2026-09-12, gate on the main
   * screenshot only: 2 of 12 live requests still carried a board with no puzzle
   * in it, and both were `freshsolve_*` frames.
   */
  // `__dirname` is src/ under tsx and .test-build/ when compiled; both are one
  // level under js/, which is the idiom contract.test.ts already uses.
  const src = fs.readFileSync(resolve(__dirname, '..', 'src', 'solver.ts'), 'utf8');
  const at = src.indexOf('`freshsolve_${Date.now()}');
  assert.notEqual(at, -1, 'could not find the freshness re-solve frame');
  const before = src.slice(Math.max(0, at - 1200), at);
  assert.ok(
    before.includes('await this.waitForBoardPainted(captchaElement);'),
    'the freshness re-solve must wait for the board to paint before grabbing a '
    + 'fresh frame — it fires precisely when the board is mid-rebuild',
  );
});
