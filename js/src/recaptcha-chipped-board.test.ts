/**
 * Regression: a reCAPTCHA board that TICKS a clicked tile is already answered.
 *
 * reCAPTCHA replies to a click in one of exactly two ways, and which one it
 * picks is the entire difference between the two kinds of board:
 *
 *   - the small blue CHIP in the tile's top-left corner — the widget KEPT the
 *     photo. Nothing more is coming; the selection IS the answer and the only
 *     thing left to do is press Verify.
 *   - the large blue CHECK across the middle of the tile — the widget is
 *     SWAPPING that photo out. What arrives underneath may match as well, so the
 *     board has to be read again, which is what the multi-round driver is for.
 *
 * A widget that swaps one clicked cell swaps them all, so a chip and a centred
 * check are never on one board at once. One look at the tiles we just clicked
 * therefore settles which board this is, and the CV layer already tells the two
 * apart: the chip is what it reports as `selected`.
 *
 * The driver used to watch only for the swap. On a chipping board the chip's
 * ARRIVAL — the photo zooms out, a blue disc appears — reads as `changing`, i.e.
 * exactly like the first frame of a swap, so the driver waited for a replacement
 * that was never coming and then spent a second inference to be told the board
 * was `done`. One wasted model call on every static reCAPTCHA.
 *
 * The Python driver is pinned on the same two boards in
 * `python/tests/test_recaptcha_chipped_board.py`; the two halves must agree.
 *
 * Fakes rather than a browser: what is under test is the DECISION the driver
 * makes from what the CV layer reports, and that is observable without Chrome.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

// 3x3, 100px cells, in screenshot pixel space.
const GRID = [
  [0, 0, 100, 100], [100, 0, 200, 100], [200, 0, 300, 100],
  [0, 100, 100, 200], [100, 100, 200, 200], [200, 100, 300, 200],
  [0, 200, 100, 300], [100, 200, 200, 300], [200, 200, 300, 300],
];
const ELEMENT_BOX = { x: 0, y: 0, width: 300, height: 300 };
const GRID_ARG = { boxes: GRID, size: 3 as const, screenshotW: 300, screenshotH: 300 };
// Normalised centre of the middle cell -> cell 5.
const CELL_5: [number, number, number, number] = [0.4, 0.4, 0.6, 0.6];

const CLICK_5 = { action: 'click', target_bounding_boxes: [CELL_5] };
const DONE = { action: 'done' };

interface Log { rounds: number; clicked: any[]; waits: number; submits: number; }

/**
 * A solver with everything around the grid driver stubbed out. `states` is what
 * the CV layer reports on every poll, `answers` one model answer per round.
 */
function driver(states: any, answers: any[]): { solver: any; log: Log } {
  const solver: any = new CaptchaKrakenSolver({
    // The real windows are seconds long and there is no browser here to wait
    // for; the decisions under test do not depend on how long a poll takes.
    recaptchaFadeOnsetGraceMs: 60,
    recaptchaDynamicFadePollMs: 1,
    recaptchaDynamicFadeWaitMs: 20,
  });
  const log: Log = { rounds: 0, clicked: [], waits: 0, submits: 0 };

  solver.waitForGridCellsLoaded = async () => true;
  solver.gridCellStates = async () => states;
  solver.hoverCell = async () => {};
  solver.emitStep = async () => {};
  solver.saveImageForDebug = () => {};
  solver.archiveLatestDebugRun = () => {};
  solver.initGridDebug = () => {};
  solver.gridDebug = () => {};
  // The freshness guard is exercised for real by the Python suite; here it
  // stands in for the model so a round is one call and one answer.
  solver.solveFrameFreshnessGuarded = async () => {
    const answer = answers[Math.min(log.rounds, answers.length - 1)];
    log.rounds += 1;
    return { actions: [answer], token_usage: [{ total_tokens: 1 }] };
  };
  solver.executeClick = async (_p: any, _el: any, action: any) => {
    log.clicked.push(action.target_bounding_box);
  };
  solver.waitForAnyClickedTileLoaded = async () => { log.waits += 1; return true; };
  solver.getVerifyButton = async () => ({});
  solver.moveAndClick = async () => { log.submits += 1; };
  return { solver, log };
}

/** An element whose screenshots land in a real temp file the driver deletes. */
function fakeElement(): any {
  return {
    screenshot: async () => Buffer.from(''),
    contentFrame: async () => ({}),
    boundingBox: async () => ({ ...ELEMENT_BOX }),
  };
}

const session = () => ({
  gridBoxes: GRID,
  elementBox: { ...ELEMENT_BOX },
  scaleX: 1, scaleY: 1, screenshotW: 300, screenshotH: 300,
});

const solve = (solver: any) =>
  solver.solveRecaptchaGrid({}, fakeElement(), 1, null, GRID_ARG, { ...ELEMENT_BOX });

test('a chipped tile submits without a second inference', async () => {
  // The board ticked cell 5 and kept the photo: `selected` names it, and it also
  // reads as `changing` because the chip is still animating in — which is what
  // used to be mistaken for the first frame of a swap.
  const { solver, log } = driver(
    { empty: [], changing: [5], loaded: [1, 2, 3, 4, 6, 7, 8, 9], selected: [5] },
    [CLICK_5, DONE],
  );
  const result = await solve(solver);

  assert.equal(log.rounds, 1, 'a ticked board must not be read a second time');
  assert.deepEqual(log.clicked, [CELL_5]);
  assert.equal(log.waits, 0, 'nothing is being replaced, so there is nothing to wait for');
  assert.equal(log.submits, 1);
  assert.equal(result.didInteract, true);
  assert.deepEqual(result.tokenUsage, [{ total_tokens: 1 }]);
});

test('a swapping tile still costs another round', async () => {
  // The other board: cell 5 blanked to white on its way to a new photo, so the
  // answer is not known yet and the driver must look again.
  const { solver, log } = driver(
    { empty: [5], changing: [], loaded: [1, 2, 3, 4, 6, 7, 8, 9], selected: [] },
    [CLICK_5, DONE],
  );
  const result = await solve(solver);

  assert.equal(log.rounds, 2, 'a replaced tile has to be read once it lands');
  assert.equal(log.waits, 1);
  assert.equal(log.submits, 1);
  assert.equal(result.didInteract, true);
});

test('one chip among the clicked tiles is not a verdict', async () => {
  // All-or-nothing, on purpose. The two states never share a board, so a partial
  // reading is a MISREAD, and the two mistakes do not cost the same: calling a
  // swapping board finished submits half an answer and burns the attempt, while
  // calling a chipped board unfinished costs one inference.
  const { solver } = driver({ empty: [], changing: [5, 6], loaded: [], selected: [5] }, []);
  const seen = await solver.watchClickedTiles({}, fakeElement(), session(), [5, 6]);

  assert.equal(seen.chipped, false);
  assert.deepEqual(seen.loading, [5, 6]);
});

test('a chip on a tile we did not click is not a verdict', async () => {
  // Only the tiles this round clicked can answer the question. A chip elsewhere
  // is a click from an earlier round (or the user's own), and says nothing about
  // what the widget just did with ours.
  const { solver } = driver({ empty: [], changing: [], loaded: [5], selected: [2] }, []);
  const seen = await solver.watchClickedTiles({}, fakeElement(), session(), [5]);

  assert.equal(seen.chipped, false);
});

/* ──────────────────────────────────────────────────────────────────────────
 * Two things the grid driver was paying for twice, or not at all.
 *
 * Both were found in the phase budget of a solve that SUCCEEDED, which is why
 * neither had ever shown up as a failure:
 *
 *     [BUDGET] solve 5.9s — 0.7s useful (11%), 5.3s waiting
 *     [BUDGET]   grid-load                1.50s  x2      <- paid twice
 *     [BUDGET]   post-submit-delay        1.48s  x1      <- should not exist
 *
 * 1. The Verify press is an INTERACTION, and this driver never said so. It sets
 *    `performedAction` when it clicks a TILE; a round that answers `done`
 *    clicks nothing, presses Verify and returns false. The caller reads that as
 *    "this round did nothing", sleeps `postSolveDelayMs` flat instead of
 *    polling for the verdict (~1s of dead time), and throws "performed no
 *    interactions" if the widget has not finished tearing down — on an answer
 *    that was correctly sent. Same bug `empty-answer-submits.test.ts` pins in
 *    the OTHER driver; that one was fixed and this one was not.
 *
 * 2. The grid-load wait was paid twice, back to back. `solveSingle` waits for
 *    the cells, reads the grid boxes off the loaded board, sees a 3x3 and hands
 *    over here — whose round 1 opened by waiting for the same board again.
 *    Rounds 2..N still wait, because by then this driver has clicked and the
 *    tiles really are reloading.
 *
 * The Python half is `python/tests/test_grid_round_pays_once.py`.
 * ────────────────────────────────────────────────────────────────────────── */

/** `driver`, plus a count of how many times the grid-load wait was paid. */
function countingDriver(states: any, answers: any[]) {
  const { solver, log } = driver(states, answers);
  const counted = log as Log & { gridLoads: number };
  counted.gridLoads = 0;
  solver.waitForGridCellsLoaded = async () => { counted.gridLoads += 1; return true; };
  return { solver, log: counted };
}

const CHIPPED = { empty: [], changing: [5], loaded: [1, 2, 3, 4, 6, 7, 8, 9], selected: [5] };
const SWAPPING = { empty: [5], changing: [], loaded: [1, 2, 3, 4, 6, 7, 8, 9], selected: [] };

test('a round that only presses Verify still reports an interaction', async () => {
  const { solver, log } = countingDriver(CHIPPED, [DONE]);
  const result = await solve(solver);

  assert.deepEqual(log.clicked, [], 'the model said `done`; nothing should be clicked');
  assert.equal(log.submits, 1, 'a `done` answer is submitted by pressing Verify');
  assert.equal(result.didInteract, true,
    'the driver pressed Verify and reported that it did nothing. The caller then '
    + 'sleeps postSolveDelayMs instead of polling for the verdict, and throws '
    + "'performed no interactions' if the widget has not vanished yet — on an "
    + 'answer that was correctly sent.');
});

test('a round-cap exit still reports no interaction', async () => {
  // The guard against "just return true". A board that keeps answering `wait`
  // never submits and never clicks, so it genuinely did nothing — and the
  // caller's infinite-loop guard is the only thing that ends it.
  const { solver, log } = countingDriver(SWAPPING, [{ action: 'wait' }]);
  const result = await solve(solver);

  assert.equal(log.submits, 0, 'nothing was answered, so nothing may be submitted');
  assert.equal(result.didInteract, false);
});

test('round one inherits the caller`s grid-load wait', async () => {
  const { solver, log } = countingDriver(CHIPPED, [DONE]);
  await solve(solver);

  assert.equal(log.gridLoads, 0,
    'round 1 waited for the grid to load; solveSingle has just done exactly '
    + 'that and nothing has touched the board in between');
});

test('later rounds still wait for the board they changed', async () => {
  const { solver, log } = countingDriver(SWAPPING, [CLICK_5, DONE]);
  await solve(solver);

  assert.equal(log.rounds, 2, 'a replaced tile has to be read once it lands');
  assert.equal(log.gridLoads, 1,
    'round 2 opens on a board this driver has just clicked, so it must wait for '
    + 'the replacement to paint before the model reads it');
});
