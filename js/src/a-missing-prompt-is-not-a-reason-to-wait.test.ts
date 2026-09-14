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
  const { frame, iframe } = rig(true);
  frame.waitForFunction = async () => { throw new Error('still painting'); };
  await (solver() as any).waitForHcaptchaChallengeImages(iframe);
});
