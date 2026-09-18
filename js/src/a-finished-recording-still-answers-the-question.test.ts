// The recording is the evidence; the ask it overlapped decides nothing.
//
// A speculative round films the widget while the model reads one screen of it, then asks the film whether
// the board cycles. The ask is what takes the time — so on a slow round the recording is already over by
// the time it is questioned, and the verdict then fell back to "a screen came back", ignoring the screens
// the film actually holds. A board that cycled for its whole window read as a still, was answered from one
// screen, clicked, and refused: an extra press on a widget that scores behaviour.
//
// These drive the real recorder with a fake camera, because the defect was in when the film was READ, and
// a fake recorder cannot be wrong about that.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

import { CaptchaKrakenSolver } from './solver';

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** A recorder over a board whose screens are `screen(i)`, filmed for ~300ms at 100fps. */
function recording(screen: (i: number) => string) {
  const solver: any = new CaptchaKrakenSolver({
    videoBurstDurationMs: 200,
    videoBurstMaxMs: 300,
    videoBurstFps: 100,
  });
  let i = 0;
  solver.shot = async (_el: any, dest: string) => fs.writeFileSync(dest, screen(i++));
  return solver.startKeyframeBurst({} as any);
}

test('a board that cycled for its whole window is animated, however late it is asked', async () => {
  const rec = recording((i) => `screen-${i}`);
  // The still ask a real round overlaps this with, outliving the recording.
  await delay(600);
  assert.equal(await rec.verdict(), true,
    'a finished film of a board that never stopped changing was read as a still');
  assert.ok(rec.screensSeen() > 6, `only ${rec.screensSeen()} screens — the camera did not run`);
  await rec.abandon();
});

test('a board that never changed is still a still, asked at the same moment', async () => {
  const rec = recording(() => 'one-screen');
  await delay(600);
  assert.equal(await rec.verdict(), false, 'a static board was filmed and called animated');
  await rec.abandon();
});

test('a board that moved once and settled is not a cycle', async () => {
  const rec = recording((i) => (i < 2 ? 'painting' : 'at-rest'));
  await delay(600);
  assert.equal(await rec.verdict(), false, 'a board that changed once and stopped was called animated');
  await rec.abandon();
});
