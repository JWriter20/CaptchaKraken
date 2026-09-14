// Playwright's default 30s stability wait cost ten of a twelve-second solve; the bound is 2000ms to match the Python port.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

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
