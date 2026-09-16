// The plan is reused across rounds so one animated board costs one inference; it is dropped when the gate never saw its screen,
// and when the widget REFUSED it, because a refused answer is identical next round by construction and would spend the whole
// no-progress fence re-pressing itself. Per-round cleanup must not delete the keyframe dir a live plan still holds.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

const ANSWER = {
  actions: [{ action: 'click', target_bounding_boxes: [[0.4, 0.4, 0.5, 0.5]], frame: 3 }],
  token_usage: [],
};

function animatedSolver() {
  const solver: any = new CaptchaKrakenSolver({});
  let bursts = 0;
  let inferences = 0;
  solver.recordKeyframeBurst = async () => { bursts++; return `/tmp/ck_burst_fake_${bursts}`; };
  solver.getAnimatedSolution = async () => { inferences++; return ANSWER; };
  solver.discardAnimatedPlan = function () { this.animatedPlan = null; };
  return { solver, counts: () => ({ bursts, inferences }) };
}

async function round(solver: any): Promise<void> {
  if (solver.animatedPlan) return;
  const burstDir = await solver.recordKeyframeBurst();
  const response = await solver.getAnimatedSolution(burstDir);
  solver.animatedPlan = { burstDir, response };
}

test('a second round costs no burst and no inference', async () => {
  const { solver, counts } = animatedSolver();
  await round(solver);
  await round(solver);
  await round(solver);
  assert.deepEqual(counts(), { bursts: 1, inferences: 1 },
    'the board was re-recorded or re-asked about; one click needs one of each');
});

test('the plan is dropped when the gate never saw its screen', async () => {
  const { solver } = animatedSolver();
  await round(solver);
  assert.ok(solver.animatedPlan, 'nothing was planned');

  solver.discardAnimatedPlan();
  assert.equal(solver.animatedPlan, null,
    'a spent plan would re-click a cell chosen from pictures that are gone');
});

test('a refused answer drops the plan, so the next round asks again', async () => {
  const { solver } = animatedSolver();
  await round(solver);
  const first = solver.animatedPlan;
  assert.ok(first, 'nothing was planned');

  // What `noteAnswer` returning true does: the widget refused it, and reusing it would press the same
  // thing until the fence trips three rounds later with half the solve budget unspent.
  solver.discardAnimatedPlan();
  await round(solver);
  assert.ok(solver.animatedPlan, 'the next round had nothing to ask with');
  assert.notEqual(solver.animatedPlan, first, 'the refused plan came back');
});

test('a new solve starts with no plan', async () => {
  const { solver } = animatedSolver();
  await round(solver);
  solver.resetSolveState();
  assert.equal(solver.animatedPlan, null,
    'a plan leaking into the next captcha would answer it with the previous one');
});

test('the recorded frames outlive the round that made them', () => {
  const fs = require('node:fs') as typeof import('fs');
  const path = require('node:path') as typeof import('path');
  const src = fs.readFileSync(
    path.resolve(__dirname, '..', 'src', 'solver.ts'), 'utf8');
  assert.match(
    src, /this\.animatedPlan\?\.burstDir !== burstDir/,
    'the round-end cleanup no longer spares the directory the plan holds',
  );
});
