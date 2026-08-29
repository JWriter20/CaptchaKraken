/**
 * The cursor drift during inference must stop when the thinking stops.
 *
 * While the model generates, this port drifts the cursor over the challenge so
 * the mouse behaves like a hand weighing the options instead of freezing. The
 * drift loop is a move, then a human dwell of 180-540ms, repeat — and it was
 * asked to stop by setting a flag the loop only reads BETWEEN dwells. So every
 * inference ended with the solver waiting out a pause nobody was watching, on
 * the one boundary where there is real work queued behind it: the answer has
 * arrived and there is a tile to click.
 *
 * Paid once per model call, per round. The Python port pays none of it, because
 * it does not wander at all — which is part of why the same fixture measured
 * seconds slower on this port.
 *
 * The dwell is still 180-540ms of genuine idle time while the model is
 * genuinely still thinking. All that changed is that it is interruptible.
 *
 * The test asks for the cheapest possible case: an answer that is ALREADY
 * there. The drift loop opens with a 120-300ms pause before its first glance,
 * so an uninterruptible one cannot hand back in under 120ms however fast the
 * model was. Best of five, so a loaded box cannot make a broken build look
 * fixed — only make a fixed one look broken, which fails loudly rather than
 * quietly.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

/** The shortest opening pause the drift loop takes before its first glance. */
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
  // Same rule as hoverCell: drifting a cursor that does not exist would emit
  // mousemove at a touch-only widget. Kept here because the wake path must not
  // become a new reason to enter the loop.
  const solver = wanderer();
  solver.human.hovers = false;
  let moves = 0;
  solver.human.move = async () => { moves += 1; };

  await solver.withIdleWander({}, element, async () => {
    await new Promise((r) => setTimeout(r, 50));
  });
  assert.equal(moves, 0);
});
