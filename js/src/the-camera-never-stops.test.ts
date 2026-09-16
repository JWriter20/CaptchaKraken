// A one-shot burst can only show the model the screens that fell inside its window, and the answer it
// produces is identical next round by construction — greedy sampling, same frames. So a board whose answer
// lived on a screen the window missed had no second chance: the stored answer was re-pressed until the
// no-progress fence tripped, three rounds in, with half the solve budget unspent. Measured live on
// 2026-09-15, hCaptcha's animated board read 2/15 and 6/12 on the two served models that way, against 36/36
// on the driver that still re-filmed every round.
//
// The camera now runs for the whole solve and every round slices a STRICTLY LONGER film. These tests drive
// the real recorder — a fake `shot` is the only stub in the filming path — because the bug was in when the
// recording stopped, and a fake recorder cannot be wrong about that.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

import { CaptchaKrakenSolver } from './solver';

/** The gap a real round has between answering and being told no: the outer loop's verdict wait. */
const verdictWait = () => new Promise((r) => setTimeout(r, 150));

const BOX = { x: 0, y: 0, width: 100, height: 100 };
const WIDGET = {
  el: { contentFrame: async () => null, boundingBox: async () => BOX },
  at: {},
  vendor: 'geetest',
  role: 'challenge',
};

/** One answer, every time: the point is that the ASK happens again, not that the answer changed. */
const ANSWER = {
  actions: [{ action: 'click', target_bounding_boxes: [[0.4, 0.4, 0.5, 0.5]], frame: 1 }],
  token_usage: [],
};

function cyclingSolver() {
  const solver: any = new CaptchaKrakenSolver({
    // A 3-screen cycle at 50fps closes in well under the floor, so the test costs ~0.3s rather than ~4s.
    videoBurstDurationMs: 120,
    videoBurstFps: 50,
    speculativeBurstEnabled: false,
  });
  const slices: number[] = [];
  const films: { events: string[] }[] = [];
  let frame = 0;

  solver.answerBox = async () => null;
  solver.classifyByRecording = async () => 'animated';
  solver.isCaptchaSolved = async () => false;
  solver.waitForBoardPainted = async () => ({ waitedMs: 0 });
  solver.getVerifyButton = async () => null;
  solver.captchaFrameChangedSince = async () => false;
  solver.executeClick = async () => {};
  solver.emitStep = async () => {};
  // Three distinct screens on a loop: enough for the recorder to see a screen come back and call it a cycle.
  solver.shot = async (_el: any, dest: string) => fs.writeFileSync(dest, `screen-${frame++ % 3}`);
  solver.getSolution = async () => ANSWER;
  solver.getAnimatedSolution = async (dir: string) => {
    slices.push(fs.readdirSync(dir).length);
    return ANSWER;
  };

  // The real recorder, with only pause/resume observed.
  const realStart = solver.startKeyframeBurst.bind(solver);
  solver.startKeyframeBurst = (el: any, continuous?: boolean) => {
    const rec = realStart(el, continuous);
    const events: string[] = [];
    films.push({ events });
    return {
      ...rec,
      pause: () => { events.push('pause'); rec.pause(); },
      resume: () => { events.push('resume'); rec.resume(); },
    };
  };
  return { solver, slices, films };
}

test('the camera survives the round that sliced it', async () => {
  const { solver } = cyclingSolver();
  await solver.solveSingle({}, WIDGET, 1, null);
  assert.ok(solver.animatedFilm, 'the recording was ended by the round that used it, so the next round has nothing longer to ask with');
  await solver.stopAnimatedFilm();
});

test('a refused answer is re-asked against a longer film, not re-pressed', async () => {
  const { solver, slices } = cyclingSolver();

  await solver.solveSingle({}, WIDGET, 1, null);          // films, asks, clicks
  const film = solver.animatedFilm;
  await verdictWait();
  await solver.solveSingle({}, WIDGET, 2, null);          // reuses, the widget refused it, plan is dropped
  await verdictWait();
  await solver.solveSingle({}, WIDGET, 3, null);          // asks again, over a film 2 rounds longer

  assert.equal(slices.length, 2, 'the refused answer was re-pressed instead of re-asked');
  assert.ok(slices[1] > slices[0],
    `the second ask saw ${slices[1]} frames against the first ask's ${slices[0]}; a re-ask over the same film asks the same question`);
  assert.equal(solver.animatedFilm, film, 'the camera was restarted rather than left running');
  await solver.stopAnimatedFilm();
});

test('the camera does not film our own answer landing', async () => {
  const { solver, films } = cyclingSolver();
  await solver.solveSingle({}, WIDGET, 1, null);
  assert.deepEqual(films[0].events, ['pause'],
    'a frame of the board wearing our clicks is not a screen the board ever showed on its own');
  await solver.stopAnimatedFilm();
});

test('the film ends with the solve, not with the round', async () => {
  const { solver } = cyclingSolver();
  await solver.solveSingle({}, WIDGET, 1, null);
  const dir = (solver.animatedFilm as any) && solver.animatedPlan?.burstDir;
  assert.ok(dir, 'nothing was planned');
  await solver.stopAnimatedFilm();
  assert.equal(solver.animatedFilm, null, 'a camera left running outlives the page it is filming');
});

test('a refused answer with no camera still re-asks on the frames it has', async () => {
  // #41's path, and it has to keep working: the plan can outlive the film — the board changed, the camera
  // was stopped, and only the slice the answer came from is left. Re-asking on that is worse than re-asking
  // on a longer one, and much better than pressing the refused answer again.
  const { solver, slices } = cyclingSolver();
  await solver.solveSingle({}, WIDGET, 1, null);
  const held = solver.animatedPlan.burstDir;

  // How this arises for real: the speculative path banks a plan from a burst it started itself and never
  // installs it as `animatedFilm`, so a refusal there leaves frames with no camera behind them.
  // Abandon rather than just drop the handle: a recorder nobody holds keeps filming to its own ceiling.
  // The plan's slice is a separate hardlinked directory, so it outlives the film it was cut from.
  await solver.animatedFilm.abandon();
  solver.animatedFilm = null;
  solver.animatedPlan = { burstDir: held, response: null };
  await solver.solveSingle({}, WIDGET, 2, null);

  assert.equal(slices.length, 2, 'the refused answer was not re-asked');
  assert.equal(solver.animatedPlan.burstDir, held, 'it filmed afresh instead of using the frames in hand');
});

test('stopping a camera that is not the current one leaves the current one alone', async () => {
  const { solver } = cyclingSolver();
  await solver.solveSingle({}, WIDGET, 1, null);
  const film = solver.animatedFilm;
  assert.ok(film, 'nothing was filmed');
  await solver.stopAnimatedFilm();
  assert.equal(solver.animatedFilm, null);
  await solver.stopAnimatedFilm();            // idempotent: a second stop must not throw
  assert.equal(solver.animatedFilm, null);
});
