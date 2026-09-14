/**
 * A readiness gate must not block on an element that is not there.
 *
 * `waitForHcaptchaChallengeImages` waits for a board to finish painting before
 * an inference is spent on it. It opened with its own
 * `waitForSelector('.prompt-text', {state: 'visible'})`, and that REJECTS when
 * the element does not exist — so a challenge with no `.prompt-text` paid the
 * whole `hcaptchaImagesTimeoutMs` and then, because the body is best-effort,
 * carried on as if nothing had happened. Silent, and per BOARD.
 *
 * Measured 2026-09-13 over the Tier 3 hCaptcha fixtures, none of which draw a
 * `.prompt-text` (the instruction is in the rendered pixels, as on several real
 * hCaptcha variants):
 *
 *     grocery_list          1 board    hcaptcha-images  3.0s   solved
 *     click_blocked_lines   5 boards   hcaptcha-images 12.0s   TIMED OUT
 *     tower_stack           7 boards   hcaptcha-images 18.0s   TIMED OUT
 *
 * The cost lands on whichever puzzle takes the most rounds — the ones with the
 * least budget left to lose. Both types solved 2/2 before the client began
 * recognising these boards as hCaptcha at all, and 0/2 after.
 *
 * Mirror of test_a_missing_prompt_is_not_a_reason_to_wait.py — CLAUDE.md 1c.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import { CaptchaKrakenSolver } from './solver';

function rig(hasPrompt: boolean) {
  const seen = { selectors: [] as string[], functionWaits: 0 };
  const frame = {
    async waitForSelector(selector: string) {
      seen.selectors.push(selector);
      if (selector === '.prompt-text' && !hasPrompt) {
        throw new Error(`Timeout waiting for selector ${selector}`);
      }
    },
    async waitForFunction() {
      seen.functionWaits += 1;
    },
  };
  const iframe = { async contentFrame() { return frame; } };
  return { seen, frame, iframe };
}

const solver = () => new CaptchaKrakenSolver({ apiKey: 'x' } as any);

test('a board without a prompt still reaches the image check', async () => {
  // THE REGRESSION: the prompt wait rejected, the catch swallowed it, and the
  // poll that actually looks at the imagery never ran.
  const { seen, iframe } = rig(false);
  await (solver() as any).waitForHcaptchaChallengeImages(iframe);
  assert.equal(seen.functionWaits, 1,
    'the gate never polled the imagery — it spent the whole timeout on a '
    + 'prompt element this board does not have, on every board');
});

test('the prompt is not waited for as a separate selector', async () => {
  const { seen, iframe } = rig(true);
  await (solver() as any).waitForHcaptchaChallengeImages(iframe);
  assert.ok(!seen.selectors.includes('.prompt-text'),
    'the prompt is being waited for by selector again; a board without one '
    + 'will pay the full timeout per round');
});

test('a board with a prompt is still gated on the imagery', async () => {
  const { seen, iframe } = rig(true);
  await (solver() as any).waitForHcaptchaChallengeImages(iframe);
  assert.equal(seen.functionWaits, 1, 'the gate became a no-op');
});

test('a detached frame is not an error', async () => {
  const iframe = { async contentFrame() { return null; } };
  await (solver() as any).waitForHcaptchaChallengeImages(iframe);
});

test('the poll itself is still allowed to time out', async () => {
  // A board that never finishes painting falls through to the screenshot; the
  // fail-fast path downstream still covers a genuinely unsupported puzzle.
  const { frame, iframe } = rig(true);
  frame.waitForFunction = async () => { throw new Error('still painting'); };
  await (solver() as any).waitForHcaptchaChallengeImages(iframe);
});
