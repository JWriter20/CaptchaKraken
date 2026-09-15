// `waitForSelector('.prompt-text')` rejects when the element is absent, hCaptcha fixtures draw none, and the many-round types paid
// 12-18s of timeouts per solve.
import assert from 'node:assert/strict';
import test from 'node:test';

import { CaptchaKrakenSolver } from './solver';
import { SELECTORS } from './selectors';
import { Vendor } from './kinds';

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
  return { seen, frame };
}

const wait = (frame: any) => (new CaptchaKrakenSolver({ apiKey: 'x' } as any) as any).waitForBoardImages(frame, SELECTORS[Vendor.HCAPTCHA]);

test('a board without a prompt still reaches the image check', async () => {
  const { seen, frame } = rig(false);
  await wait(frame);
  assert.equal(seen.functionWaits, 1,
    'the gate never polled the imagery — it spent the whole timeout on a '
    + 'prompt element this board does not have, on every board');
});

test('the prompt is not waited for as a separate selector', async () => {
  const { seen, frame } = rig(true);
  await wait(frame);
  assert.ok(!seen.selectors.includes('.prompt-text'),
    'the prompt is being waited for by selector again; a board without one '
    + 'will pay the full timeout per round');
});

test('a board with a prompt is still gated on the imagery', async () => {
  const { seen, frame } = rig(true);
  await wait(frame);
  assert.equal(seen.functionWaits, 1, 'the gate became a no-op');
});

test('the poll itself is still allowed to time out', async () => {
  const { frame } = rig(true);
  frame.waitForFunction = async () => { throw new Error('still painting'); };
  await wait(frame);
});
