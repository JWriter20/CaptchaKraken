/**
 * Regression: the frame gate was OFF on every real animated captcha, and when
 * it did fire the pointer still had to travel afterwards.
 *
 * GeeTest's svg variant advances through 2-3 screens of fresh glyphs, dwelling
 * ~1.5s on each. The model answers WHERE and WHEN — `frame` names the screen —
 * and `waitForKeyframe` exists to hold the click until that screen is back up.
 * Live it solved 1/14 on the 27B and 1/8 on v1.2. Three separate faults, each
 * enough on its own:
 *
 * 1. THE GATE NEVER RAN. It skipped whenever `keyframeMode === 'even'`, on the
 *    reasoning that `even` means no state recurs. It does not: `even` means the
 *    SLICER could not PROVE recurrence, and it cannot, because `_detect_cycle`
 *    requires a state to be seen coming back and only ONE pass fits in a 4s
 *    burst. Measured over 60 real clips: 32 sliced `even` while sitting on 2-3
 *    steady screens covering 95-100% of the burst. So the driver clicked
 *    whatever screen happened to be up — right about one time in three, which
 *    is what 1/14 looks like.
 *
 *    The skip was added for a real measurement (2026-08-19,
 *    an hCaptcha rotating-object animation: 6.0s of a 28.8s solve spent waiting for a
 *    frame that could not return) and that case must keep its behaviour. It
 *    does: a rotation is nearly all transition, so it decomposes into no steady
 *    holds at all. Measured across all five continuous hCaptcha video types,
 *    12 clips each: `steady_screens` 0 for 60 of 60. GeeTest svg: 2-3 for every
 *    real capture. The two families separate cleanly, which is why the gate now
 *    keys on the screen COUNT rather than on the slicing mode.
 *
 * 2. THE POINTER TRAVELLED AFTER THE MATCH. The wait was followed by
 *    `executeClick`, which starts with a humanised move — measured 274ms p10 /
 *    398ms p50 / 647ms max across a 340x384 widget, i.e. 27-36% of a 1500ms
 *    dwell, spent after we had already confirmed the right screen was up. So
 *    the gate could succeed and the click still land on the next screen. The
 *    pointer is now PARKED on the target before the wait begins and pressed in
 *    place, which is what `move()`'s same-point short-circuit makes free.
 *
 * 3. THE BUDGET WAS SHORTER THAN THE CYCLE. 6000ms against a 3-screen cycle
 *    measured at 4.5s median and 8.1s worst case, so the worst case could not
 *    fit even in principle.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver, SOLVE_DEFAULTS } from './solver';

/** A solver whose keyframe probe is counted rather than run. */
function gated(opts: { mode: string | null; screens: number; matchAfter?: number }) {
  const solver: any = new CaptchaKrakenSolver({ keyframeWaitPollMs: 1 });
  solver.keyframeMode = opts.mode;
  solver.keyframeSteadyScreens = opts.screens;
  let probes = 0;
  solver.runCvTool = async () => {
    probes += 1;
    return { match: probes >= (opts.matchAfter ?? 1), diff: 0.01 };
  };
  const element: any = { screenshot: async () => undefined };
  return { solver, element, probes: () => probes };
}

test('a clip that sits on steady screens waits, even when sliced `even`', async () => {
  // The live failure. Every real geetest_v4_svg burst is exactly this shape.
  const { solver, element, probes } = gated({ mode: 'even', screens: 3 });
  const matched = await solver.waitForKeyframe(element, '/tmp/kf.png', 0.5, 0.5);
  assert.equal(matched, true, 'the gate must run for a board that holds screens');
  assert.ok(probes() >= 1, 'it must actually look at the widget');
});

test('a one-way animation still does not wait', async () => {
  // an hCaptcha rotating-object animation and the other four continuous types: no steady
  // holds, nothing to come back to, and waiting is pure cost. Preserved.
  //
  // `/tmp/kf.png` is not a member of a numbered keyframe set, so the local
  // check below finds no siblings to ask about and the screen count decides —
  // which is the old rule, unchanged, for every clip that cannot answer the
  // local question.
  const { solver, element, probes } = gated({ mode: 'even', screens: 0 });
  const matched = await solver.waitForKeyframe(element, '/tmp/kf.png', 0.5, 0.5);
  assert.equal(matched, false);
  assert.equal(probes(), 0, 'a clip with no steady screens must not be polled at all');
});

test('no steady screens, but the answer AREA comes back — then it waits', async () => {
  /*
   * The other half of the rule, and the reason it exists.
   *
   * `keyframeSteadyScreens` counts screens the WHOLE CLIP returns to, and it is
   * 0 for all five continuous hCaptcha video types — so the gate was refusing
   * on exactly the puzzles whose answer is the moment.
   * number_with_highest_value_video fades one digit in at a time: measured on
   * its own keyframes the whole frame differs by 0.0009-0.0099 between them
   * while the region around the answer differs by up to 0.1143, and three of
   * the six are identical there. There is plainly something to come back to.
   *
   * What must NOT happen is waiting on a clip where the chosen frame is unique —
   * a rotation, a one-way fade — because the gate could only ever time out. So
   * the question is whether the chosen keyframe's answer area MATCHES another
   * keyframe of the same clip, not merely whether it differs from them.
   */
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kfset_'));
  for (const n of ['frame_01.png', 'frame_02.png', 'frame_03.png']) {
    fs.writeFileSync(path.join(dir, n), '');
  }
  const solver: any = new CaptchaKrakenSolver({ keyframeWaitPollMs: 1 });
  solver.keyframeSteadyScreens = 0;
  let probes = 0;
  solver.runCvTool = async (tool: string) => {
    if (tool === 'match-region') probes += 1;
    return { match: true, diff: 0.0 };
  };
  const element: any = { screenshot: async () => undefined };
  try {
    const matched = await solver.waitForKeyframe(
      element, path.join(dir, 'frame_01.png'), 0.5, 0.5);
    assert.equal(matched, true, 'an answer area that recurs is worth waiting for');
    assert.ok(probes >= 2, 'it must compare against the clip, then poll the widget');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('the wait is bounded by the evidence that opened it', async () => {
  // A clip with no steady screens got in on LOCAL evidence — one burst saw the
  // area come back — so the wait may not outlast a burst. Uncapped,
  // number_with_highest_value_video ran 36s.
  const BURST_FLOOR_MS = 4000;   // videoBurstDurationMs' default, in solver.ts
  assert.ok(BURST_FLOOR_MS < SOLVE_DEFAULTS.keyframeWaitTimeoutMs,
    'the local-evidence cap must be shorter than the full budget, or it is not a cap');
});

test('a proven cycle still waits', async () => {
  const { solver, element, probes } = gated({ mode: 'cycle', screens: 3 });
  assert.equal(await solver.waitForKeyframe(element, '/tmp/kf.png', 0.5, 0.5), true);
  assert.ok(probes() >= 1);
});

test('the gate polls until the screen comes round, then reports the match', async () => {
  const { solver, element, probes } = gated({ mode: 'even', screens: 2, matchAfter: 5 });
  assert.equal(await solver.waitForKeyframe(element, '/tmp/kf.png', 0.5, 0.5), true);
  assert.equal(probes(), 5, 'it must keep looking, not answer from the first frame');
});

test('the pointer is parked on the target BEFORE the gate opens', async () => {
  // The ordering that makes the gate worth having: by the time the screen
  // matches there must be nothing left to do but press.
  const solver: any = new CaptchaKrakenSolver({ keyframeWaitPollMs: 1 });
  solver.keyframeMode = 'even';
  solver.keyframeSteadyScreens = 3;

  const events: string[] = [];
  solver.runCvTool = async () => { events.push('probe'); return { match: true, diff: 0.01 }; };
  solver.human = {
    move: async (_p: any, to: [number, number]) => { events.push(`move:${to[0]},${to[1]}`); },
    click: async (_p: any, to: [number, number]) => { events.push(`click:${to[0]},${to[1]}`); },
    pause: async () => {},
  };

  const element: any = { screenshot: async () => undefined };
  const box = { x: 100, y: 200, width: 300, height: 400 };
  await solver.clickWhenFrameMatches(
    {} as any, element,
    { action: 'click', target_bounding_box: [0.4, 0.4, 0.6, 0.6] },
    box, '/tmp/kf.png',
  );

  const moved = events.findIndex((e) => e.startsWith('move:'));
  const probed = events.indexOf('probe');
  const clicked = events.findIndex((e) => e.startsWith('click:'));
  assert.ok(moved >= 0 && probed >= 0 && clicked >= 0, `missing step in ${events.join(' ')}`);
  assert.ok(moved < probed, `pointer must be parked before the gate opens: ${events.join(' ')}`);
  assert.ok(probed < clicked, `the click must come after the match: ${events.join(' ')}`);

  // And the point pressed must be the point parked on — parking somewhere else
  // would reintroduce exactly the travel this removes.
  assert.equal(events[clicked].slice('click:'.length), events[moved].slice('move:'.length),
    `parked and pressed different points: ${events.join(' ')}`);
});

test('the wait budget can hold one worst-case cycle', () => {
  // Dwell max 2.7s x 3 screens = 8.1s (src/captchaCollection/sources.py).
  // A budget under that cannot catch the worst case however well it is aimed.
  const budget = SOLVE_DEFAULTS.keyframeWaitTimeoutMs;
  assert.ok(budget >= 8_100,
    `keyframeWaitTimeoutMs is ${budget}ms; a 3-screen cycle runs to 8100ms`);
});

test('the gate stops early once the widget is clearly a different board', async () => {
  // AFTER a successful click the board is gone, and the gate was waiting out
  // its whole 9s budget for a screen that could never return — once per solve,
  // measured as the largest single item in a 40.3s trace.
  //
  // The number it needed was already in its hand: two SCREENS of one board
  // differ by 0.0056, a different board by 0.77.
  const solver: any = new CaptchaKrakenSolver({ keyframeWaitPollMs: 1 });
  solver.keyframeMode = 'even';
  solver.keyframeSteadyScreens = 3;
  let probes = 0;
  solver.runCvTool = async () => { probes += 1; return { match: false, diff: 0.77 }; };
  const element: any = { screenshot: async () => undefined };

  const t0 = Date.now();
  const matched = await solver.waitForKeyframe(element, '/tmp/kf.png', 0.5, 0.5);
  const took = Date.now() - t0;

  assert.equal(matched, false);
  assert.ok(probes <= 5, `gave up after ${probes} polls; it should need only a few`);
  assert.ok(took < 2_000, `spent ${took}ms deciding the board was gone`);
});

test('a WRONG SCREEN of the right board is still waited for', async () => {
  // The whole point of the gate. A different screen reads ~0.0056, nowhere near
  // the "not this board" bar, so it must keep polling.
  const solver: any = new CaptchaKrakenSolver({ keyframeWaitPollMs: 1 });
  solver.keyframeMode = 'even';
  solver.keyframeSteadyScreens = 3;
  let probes = 0;
  solver.runCvTool = async () => {
    probes += 1;
    return probes >= 8 ? { match: true, diff: 0.0 } : { match: false, diff: 0.0056 };
  };
  const element: any = { screenshot: async () => undefined };
  assert.equal(await solver.waitForKeyframe(element, '/tmp/kf.png', 0.5, 0.5), true);
  assert.equal(probes, 8, 'it gave up on the right board while the wrong screen was up');
});

test('a cycling board is recorded after ONE round, not two', async () => {
  // The freshness guard already watches the frame during inference. Seeing it
  // change TWICE in one round, with nothing clicked, is a cycling board — and
  // it used to re-solve instead, then wait for the same answer to come back a
  // round later. Two still inferences and ~10s of a 40s solve.
  const solver: any = new CaptchaKrakenSolver({});
  let changes = 0;
  solver.captchaFrameChangedSince = async () => { changes += 1; return true; };
  solver.answerFor = async (_k: string, run: () => any) => run();
  const el: any = { screenshot: async () => undefined };
  let queries = 0;
  await solver.solveFrameFreshnessGuarded(el, '/tmp/shot.png', async () => {
    queries += 1;
    return { actions: [], token_usage: [] };
  });
  assert.equal(solver.shouldRetryAsAnimated('unknown'), true,
    'two changes in one round and it still wants another round to be sure');
  assert.ok(queries <= 2, `${queries} inferences spent re-solving a board that never holds still`);
});
