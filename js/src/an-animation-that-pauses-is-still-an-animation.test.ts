// A board that pauses is not a board that has stopped.
//
// Two faults let `hcaptcha_item_animal_never_touches` be answered from one screen, and the gate said
// so: "THE DRIVER NEVER ASKED ABOUT THE KEYFRAMES — 0 keyframe calls, on an animated fixture."
//
// 1. The mover was too small to see. `settleDiffThreshold` was 0.01 — a board moves when 1% of its
//    pixels change. A bee crossing a meadow is 0.4%. Measured with the shared `movement_ratio`
//    220ms apart: the bee peaks at 0.00429 and cleared 0.01 on NONE of 55 polls, while rotating_obj
//    (0.01845) and tile_flip (0.05863) cleared it easily. Every still board measured 0.00000.
//
// 2. A pause was read as a settle. `settleFrames` is 2, so 440ms of quiet ends the check, and the
//    bee lands on a flower and sits. Captured at 220ms over 12s the board reads
//    `.M...........MMMMMMM.......MMMMMMM........MMMM` — the leading pair returns SETTLED before a
//    single move is seen.
//
// The python port owns the behavioural test over that captured trace. Per CLAUDE.md 1c the two
// ports must behave identically, so what this pins is that THIS port carries the same two changes
// with the same constants — a port that quietly kept 0.01 would diverge with nothing to catch it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const SOLVER = readFileSync(path.join(__dirname, '..', 'src', 'solver.ts'), 'utf8');
const TYPES = readFileSync(path.join(__dirname, '..', 'src', 'types.ts'), 'utf8');

test('the settle threshold can see a small mover on a big board', () => {
  assert.match(SOLVER, /cfg\.settleDiffThreshold \?\? 0\.002/,
    'the fallback threshold is back above the bee, which measured 0.00429');
  assert.doesNotMatch(SOLVER, /settleDiffThreshold \?\? 0\.01\b/,
    'the old 1% fallback is still here; it saw none of 55 polls on the bee board');
});

test('a board that has moved needs a longer quiet run before it counts as settled', () => {
  assert.match(SOLVER, /const quietAfterMotion = cfg\.settleFramesAfterMotion \?\? 12;/,
    'the quiet-run knob is missing, so a pause still reads as a settle');
  assert.match(SOLVER, /if \(moved\) seenMotion = true;/,
    'nothing records that the board has moved, so the longer run can never apply');
  assert.match(
    SOLVER,
    /stillStreak >= \(seenMotion && quietAfterMotion \? quietAfterMotion : settleFrames\)/,
    'the settle test does not distinguish a board that has moved from one that never did',
  );
});

test('a board that never moved still settles on the short run', () => {
  // The cost has to land only on boards that actually moved, or every still board pays for this.
  const branch = SOLVER.match(/stillStreak >= \([^)]*\)/)![0];
  assert.match(branch, /: settleFrames\)/,
    'a board that never moved would wait the long run, which every still board would pay');
});

test('the knob is declared on the public options type', () => {
  assert.match(TYPES, /settleFramesAfterMotion\?: number;/,
    'an embedder cannot pin or tune what is not on the type');
});

test('the quiet run outlasts the longest pause in the captured trace', () => {
  // 11 quiet polls is the real gap between the bee's flights; 12 is the smallest run that survives
  // it. If someone lowers this, the board it was written for goes back to being answered as a still.
  const n = Number(SOLVER.match(/cfg\.settleFramesAfterMotion \?\? (\d+)/)![1]);
  assert.ok(n > 11, `a ${n}-poll quiet run settles inside the bee's own pause`);
});
