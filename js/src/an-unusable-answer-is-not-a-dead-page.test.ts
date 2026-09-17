// An answer with nothing to execute is not proof the page is stuck.
//
// The throw exists for a driver that cannot act at all — no widget, no controls, nothing to press. An answer
// the driver could not USE is a different thing, and on an animated board it is exactly what a still expert
// returns when the board is not a still.
//
// Measured on the hosted arms, 2026-09-17: an animated hCaptcha board came back as
// `{"action": "drag", "source_bounding_box": null, ...}`, the driver logged "slide action, but the widget has
// neither a slider nor a draggable piece", performed nothing and gave up 6.0s into a 45s budget with the
// recording never taken. Three video types failed 0/2 that way while the same boards solved on the other
// adapter. One second look costs a round; giving up costs the solve.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

import { CaptchaKrakenSolver } from './solver';

const BOX = { x: 0, y: 0, width: 100, height: 100 };
const WIDGET = {
  el: { contentFrame: async () => null, boundingBox: async () => BOX },
  at: {},
  vendor: 'hcaptcha',
  role: 'challenge',
};

/** A solver whose answers execute nothing for the first `barren` rounds. */
function driver(barren: number, videoSolveEnabled = true) {
  const solver: any = new CaptchaKrakenSolver({
    videoBurstDurationMs: 120,
    videoBurstFps: 50,
    speculativeBurstEnabled: false,
    maxSolveLoops: 4,
    postSolveOutcomeTimeoutMs: 60,
    videoSolveEnabled,
  });
  const armed: boolean[] = [];
  let round = 0;

  solver.answerBox = async () => null;
  solver.classifyByRecording = async () => 'settled';
  solver.isCaptchaSolved = async () => false;
  solver.waitForBoardPainted = async () => ({ waitedMs: 0 });
  solver.getVerifyButton = async () => null;
  solver.captchaFrameChangedSince = async () => false;
  solver.executeClick = async () => {};
  solver.emitStep = async () => {};
  solver.detectCaptcha = async () => WIDGET;
  solver.hasInteractiveWidgetInDom = async () => false;
  solver.bannerKind = async () => null;
  solver.human.reset = async () => {};
  solver.isChallengeFreshlyRendered = async () => false;
  solver.shot = async (_el: any, dest: string) => fs.writeFileSync(dest, 'board');
  solver.getSolution = async () => ({ actions: [], token_usage: [] });
  solver.getAnimatedSolution = async () => ({ actions: [], token_usage: [] });
  // What the loop is really asked each round: was the second look armed going into it?
  const shouldRetry = solver.shouldRetryAsAnimated.bind(solver);
  solver.shouldRetryAsAnimated = (source: any) => {
    const fires = shouldRetry(source);
    armed.push(fires);
    return fires;
  };
  // Every round answers with nothing until `barren` is spent.
  solver.solveSingle = async () => {
    round++;
    return { didInteract: round > barren, tokenUsage: [] };
  };
  return { solver, armed, rounds: () => round };
}

test('an answer the widget cannot take buys the recording path a round', async () => {
  const { solver, rounds } = driver(1);
  await solver.solveImpl({}).catch((e: Error) => {
    assert.ok(!/performed no interactions/.test(e.message),
      'the solve was abandoned on an answer the widget could not take');
  });
  assert.ok(rounds() >= 2, `the driver gave up after ${rounds()} round(s) instead of looking again`);
});

test('a page that never takes an answer still gives up', async () => {
  const { solver, rounds } = driver(99);
  let message = '';
  await solver.solveImpl({}).catch((e: Error) => { message = e.message; });
  assert.match(message, /performed no interactions/);
  assert.equal(rounds(), 2, `gave the recording path ${rounds() - 1} rounds, not one`);
});

test('a caller with recording off gets the old behaviour', async () => {
  const { solver, rounds } = driver(99, false);
  let message = '';
  await solver.solveImpl({}).catch((e: Error) => { message = e.message; });
  assert.match(message, /performed no interactions/);
  assert.equal(rounds(), 1, 'recording is off; there is no second look to buy');
});
