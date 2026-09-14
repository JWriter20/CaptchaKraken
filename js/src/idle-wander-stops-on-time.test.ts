import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

const MIN_OPENING_PAUSE_MS = 120;

function wanderer() {
  const solver: any = new CaptchaKrakenSolver({});
  solver.human = { hovers: true, at: [0, 0], move: async () => {} };
  return solver;
}

const element = { boundingBox: async () => ({ x: 0, y: 0, width: 300, height: 300 }) };

test('an answer that is already there is handed back at once', async () => {
  const runs: number[] = [];
  for (let i = 0; i < 5; i++) {
    const solver = wanderer();
    const t0 = Date.now();
    await solver.withIdleWander({}, element, async () => 'answer');
    runs.push(Date.now() - t0);
  }

  const best = Math.min(...runs);
  assert.ok(best < MIN_OPENING_PAUSE_MS, (
    `the fastest of five runs took ${best}ms to hand back an answer that was `
    + `already available. The drift loop's opening pause is ${MIN_OPENING_PAUSE_MS}-300ms `
    + 'and it is not being woken, so every inference ends by waiting out a '
    + 'pause with a click already queued behind it.'));
});

test('a humanizer with no cursor never drifts at all', async () => {
  const solver = wanderer();
  solver.human.hovers = false;
  let moves = 0;
  solver.human.move = async () => { moves += 1; };

  await solver.withIdleWander({}, element, async () => {
    await new Promise((r) => setTimeout(r, 50));
  });
  assert.equal(moves, 0);
});
