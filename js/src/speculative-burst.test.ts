import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

const STILL = { actions: [{ action: 'click', target_bounding_boxes: [[0.1, 0.1, 0.2, 0.2]] }], token_usage: [] };
const VIDEO = { actions: [{ action: 'click', target_bounding_boxes: [[0.4, 0.4, 0.5, 0.5]], frame: 2 }], token_usage: [] };

function speculating(moved: boolean) {
  const solver: any = new CaptchaKrakenSolver({});
  const calls: string[] = [];
  let abandoned = false;
  let finished = false;
  solver.startKeyframeBurst = () => ({
    moved: () => moved,
    abandon: async () => { abandoned = true; },
    finish: async () => { finished = true; return '/tmp/ck_burst_fake'; },
  });
  solver.getSolution = async () => { calls.push('still'); return STILL; };
  solver.getAnimatedSolution = async () => { calls.push('video'); return VIDEO; };
  solver.captchaFrameChangedSince = async () => false;
  solver.answerFor = async (_k: string, run: () => any) => run();
  return { solver, calls, state: () => ({ abandoned, finished }) };
}

test('a still board pays exactly one inference and drops the recording', async () => {
  const { solver, calls, state } = speculating(false);
  const rec = solver.startKeyframeBurst();
  const still = await solver.solveFrameFreshnessGuarded(
    {} as any, '/tmp/s.png', () => solver.getSolution());
  assert.equal(rec.moved(), false);
  await rec.abandon();
  assert.deepEqual(calls, ['still'], 'a still board asked the model more than once');
  assert.equal(state().abandoned, true, 'the frames of a still board were kept');
  assert.equal(state().finished, false, 'a still board finished a recording it did not need');
  assert.ok(still);
});

test('a moving board drops the still answer and finishes the recording', async () => {
  const { solver, calls, state } = speculating(true);
  const rec = solver.startKeyframeBurst();
  await solver.solveFrameFreshnessGuarded({} as any, '/tmp/s.png', () => solver.getSolution());
  assert.equal(rec.moved(), true);
  const dir = await rec.finish();
  const video = await solver.getAnimatedSolution(dir);
  assert.deepEqual(calls, ['still', 'video'],
    'the still call is expected — it is the one that overlaps the recording');
  assert.equal(state().finished, true);
  assert.equal(video.actions[0].frame, 2, 'the answer acted on must be the video one');
});

test('reCAPTCHA never speculates', () => {
  const on: any = new CaptchaKrakenSolver({ speculativeBurstEnabled: true });
  assert.equal(on.shouldSpeculate('recaptcha', false), false);
  assert.equal(on.shouldSpeculate('hcaptcha', false), true);
  assert.equal(on.shouldSpeculate('unknown', false), true);
});

test('speculating is ON when unset', () => {
  const unset: any = new CaptchaKrakenSolver({});
  assert.equal(unset.shouldSpeculate('hcaptcha', false), true);
  assert.equal(unset.shouldSpeculate('unknown', false), true);
});

test('a distorted-text round never speculates', () => {
  const solver: any = new CaptchaKrakenSolver({ speculativeBurstEnabled: true });
  assert.equal(solver.shouldSpeculate('unknown', true), false);
});

test('it can be turned off, and off is the old behaviour', () => {
  const solver: any = new CaptchaKrakenSolver({ speculativeBurstEnabled: false });
  assert.equal(solver.shouldSpeculate('hcaptcha', false), false);
  const noVideo: any = new CaptchaKrakenSolver({ videoSolveEnabled: false });
  assert.equal(noVideo.shouldSpeculate('hcaptcha', false), false);
});

test('the speculative round does not wander the cursor over what it is filming', () => {
  const fs = require('node:fs') as typeof import('fs');
  const path = require('node:path') as typeof import('path');
  const src = fs.readFileSync(path.resolve(__dirname, '..', 'src', 'solver.ts'), 'utf8');
  const start = src.indexOf('} else if (this.shouldSpeculate(');
  const end = src.indexOf('} else {', start);
  assert.ok(start > 0 && end > start, 'could not find the speculative branch');
  const branch = src.slice(start, end);
  assert.ok(
    !/withIdleWander[\s\S]{0,200}getSolution/.test(branch),
    'the still inference inside the speculative branch wanders the cursor',
  );
});
