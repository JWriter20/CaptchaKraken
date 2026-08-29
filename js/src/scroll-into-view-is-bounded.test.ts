/**
 * Scrolling to an element must not be allowed to take thirty seconds.
 *
 * `move()` calls `scrollIntoViewIfNeeded` before every gesture — once per
 * action and once per submit. Playwright's default timeout is 30s, and it does
 * not just scroll: it waits for the element to be STABLE, i.e. to stop
 * animating. A captcha widget mid-animation is exactly the input that makes
 * that wait run long, and the element is already on screen anyway, because the
 * driver has just screenshotted it.
 *
 * MEASURED on mtcaptcha_text, fixture seed 20260730, this port:
 *
 *     [trial 1] SOLVED  12.0s
 *         # 1 initial  +0.89s   @0.9s   initial (pre-action)
 *         # 2 type     +10.13s  @11.0s  typed the code
 *
 *     phases: inference 1.5s, mouse 0.7s, settle 0.7s, verdict 0.1s
 *
 * Ten of those twelve seconds are in neither the model nor the mouse. They are
 * one un-timed-out scroll to a text box that never moved. The Python port
 * bounds it at 2000ms and has since it "turned a ~5s solve loop into minutes
 * during live testing"; this port never got that fix, and the two ports must
 * behave the same (CLAUDE.md 1c).
 *
 * On timeout the move proceeds to wherever `boundingBox()` says the element is,
 * which is what it would have done anyway.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

/** The longest a scroll may block a gesture. */
const MAX_SCROLL_WAIT_MS = 3000;

function solverWithFakePointer() {
  const solver: any = new CaptchaKrakenSolver({});
  const moves: Array<[number, number]> = [];
  solver.human = { hovers: true, at: [0, 0], move: async (_p: any, to: [number, number]) => { moves.push(to); } };
  return { solver, moves };
}

const boxed = (onScroll: (opts: any) => Promise<void>) => ({
  scrollIntoViewIfNeeded: (opts: any) => onScroll(opts),
  boundingBox: async () => ({ x: 10, y: 20, width: 100, height: 40 }),
});

test('the scroll before a gesture carries an explicit short timeout', async () => {
  let seen: any = 'never called';
  const { solver } = solverWithFakePointer();
  await solver.move({}, boxed(async (opts) => { seen = opts; }));

  assert.notEqual(seen, 'never called', 'move() no longer scrolls at all');
  assert.ok(seen && typeof seen.timeout === 'number',
    'scrollIntoViewIfNeeded was called with no timeout, so it inherits '
    + "Playwright's 30s default and waits for the element to stop animating");
  assert.ok(seen.timeout <= MAX_SCROLL_WAIT_MS,
    `the scroll may block a gesture for ${seen.timeout}ms; the element is `
    + 'already on screen, so anything past a couple of seconds is a wait for '
    + 'an animation to end, not for a scroll to happen');
});

test('a scroll that times out still lets the gesture happen', async () => {
  const { solver, moves } = solverWithFakePointer();
  await solver.move({}, boxed(async () => { throw new Error('Timeout 2000ms exceeded.'); }));

  assert.equal(moves.length, 1,
    'a scroll that timed out aborted the whole gesture. The element is where '
    + 'boundingBox says it is either way — failing here turns a slow scroll '
    + 'into a failed solve.');
});
