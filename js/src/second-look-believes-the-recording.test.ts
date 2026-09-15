// The second look is a QUESTION, and the clip it records is the answer. Declaring the board animated before
// filming it sent every board that merely failed once to the video expert, which can only answer a still with
// a frame number no widget will take — and then the answer repeated until the no-progress fence tripped.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { CaptchaKrakenSolver } from './solver';

const NO_ACTIONS = { actions: [], token_usage: [] };

function probing(moved: boolean, classified: string = 'settled'): { solver: any; asked: string[] } {
  const solver: any = new CaptchaKrakenSolver({});
  const asked: string[] = [];

  solver.answerBox = async () => null;
  solver.classifyByRecording = async () => classified;
  solver.isCaptchaSolved = async () => false;
  solver.waitForBoardPainted = async () => ({ waitedMs: 0 });
  solver.shot = async (_el: any, dest: string) => fs.writeFileSync(dest, 'board');
  solver.getVerifyButton = async () => null;
  solver.getSolution = async () => { asked.push('still'); return NO_ACTIONS; };
  solver.getAnimatedSolution = async () => { asked.push('video'); return NO_ACTIONS; };
  solver.captchaFrameChangedSince = async () => false;
  solver.startKeyframeBurst = () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ck_burst_'));
    const rested = path.join(dir, 'frame_0007.png');
    return {
      screensSeen: () => (moved ? 7 : 1),
      stableFrame: () => rested,
      verdict: async () => moved,
      abandon: async () => {},
      finish: async () => { fs.writeFileSync(rested, 'the screen it came to rest on'); return { dir, moved }; },
    };
  };
  // What a repeated answer leaves behind: the second look is armed for the next round.
  solver.repeatedAnswerSeen = true;
  return { solver, asked };
}

const WIDGET = {
  el: { contentFrame: async () => null, boundingBox: async () => null },
  at: {},
  vendor: 'geetest',
  role: 'challenge',
};

test('a clip that never moved goes back to the still expert', async () => {
  const { solver, asked } = probing(false);
  await solver.solveSingle({}, WIDGET, 2, null);
  assert.deepEqual(asked, ['still'], 'a board that never moved was sent to the video expert');
  assert.equal(solver.knownAnimated, false);
});

test('a clip that moved is the video expert’s', async () => {
  const { solver, asked } = probing(true);
  await solver.solveSingle({}, WIDGET, 2, null);
  assert.deepEqual(asked, ['video']);
  assert.equal(solver.knownAnimated, true, 'a clip that moved must hold for the rest of the solve');
});

test('the second look is spent once', async () => {
  const { solver } = probing(false);
  assert.equal(solver.shouldRetryAsAnimated('geetest'), true, 'a repeated answer arms it');
  await solver.solveSingle({}, WIDGET, 2, null);
  solver.repeatedAnswerSeen = true;
  assert.equal(solver.shouldRetryAsAnimated('geetest'), false, 'a second recording per solve');
});

test('neither flag survives into the next solve', async () => {
  const { solver } = probing(true);
  await solver.solveSingle({}, WIDGET, 2, null);
  solver.resetSolveState();
  assert.equal(solver.knownAnimated, false);
  assert.equal(solver.animatedProbeDone, false);
});

test('a measured animation is not demoted by its own clip', async () => {
  // The clip can only PROMOTE: an animation the classifier caught stays one even when the film that
  // follows ends on a settled screen, which is what a board that plays once and stops films like.
  const { solver, asked } = probing(false, 'animated');
  await solver.solveSingle({}, WIDGET, 1, null);
  assert.deepEqual(asked, ['video']);
});

test('the still it re-reads is the screen the board came to rest on', async () => {
  const { solver } = probing(false);
  let read: string | null = null;
  solver.getSolution = async (imagePath: string) => { read = fs.readFileSync(imagePath, 'utf8'); return NO_ACTIONS; };
  await solver.solveSingle({}, WIDGET, 2, null);
  assert.equal(read, 'the screen it came to rest on', 'the still was read from before the film, not after it');
});

test('a promoted board stays animated without a second look', async () => {
  const { solver, asked } = probing(true);
  await solver.solveSingle({}, WIDGET, 2, null);
  await solver.solveSingle({}, WIDGET, 3, null);
  assert.equal(solver.shouldRetryAsAnimated('geetest'), false, 'the second look is spent');
  assert.deepEqual(asked, ['video'], 'the recorded answer is reused, not re-asked');
});
