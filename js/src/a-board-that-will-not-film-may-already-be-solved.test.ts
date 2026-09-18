// A recording that catches NOTHING is not a verdict about the board.
//
// The recorder returns no frames only when every screenshot in its window failed. A still
// photographs fine, so nothing coming back does not mean "this board is static" — it means the
// widget would not screenshot at all, and the commonest reason for that is that it is CLOSING,
// because the answer was accepted.
//
// Every other failure in the solve loop asks `isCaptchaSolved` before giving up; the animated
// branch threw instead. Measured on the python port, which fails the same way: prosopo_grid_3x3
// solved 8/8 across six runs on 09-12 and 09-13, then lost four attempts on 09-17 to exactly this,
// each after its FIRST board came back from the fixture's own /fx/verify graded `solved: true`.
//
// The two ports must behave identically here, so this is the source-level half of the same test:
// both throw sites must carry the marker, and the loop must ask before it gives up.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const SOLVER = readFileSync(path.join(__dirname, '..', 'src', 'solver.ts'), 'utf8');

test('every "caught nothing" throw says so, and only for a board not proven animated', () => {
  const throws = [...SOLVER.matchAll(/no frame screenshotted[\s\S]{0,900}?throw e;/g)].map((m) => m[0]);
  assert.ok(throws.length >= 2, `expected both recorder paths, found ${throws.length}`);
  for (const site of throws) {
    // The marker must be there, and it must be CONDITIONAL. Giving a proven animated board the soft
    // landing is not a smaller bug than not having it: measured on the python port, it took the two
    // video types from 3 solved and 10 keyframe calls to 0 and 0, because the first failed film
    // spends the probe and every round after it is answered as a still.
    assert.match(site, /if \(!this\.knownAnimated\) e\.nothingFilmed = true;/,
      'a failed film either cannot be told from an animated dead end, or swallows one');
  }
});

test('the loop asks whether the board was accepted before giving up', () => {
  const branch = SOLVER.match(/if \(e\?\.nothingFilmed[\s\S]{0,1400}?\n        \}/);
  assert.ok(branch, 'the nothing-filmed branch is gone from the solve loop');
  assert.match(branch[0], /await this\.isCaptchaSolved\(page\)/,
    'the solve can still end without asking whether the answer was taken');
  assert.match(branch[0], /return done\(\)/,
    'an accepted board must end the solve as a solve');
});

test('the recovery is only for a solve that has already acted', () => {
  const branch = SOLVER.match(/if \(e\?\.nothingFilmed[\s\S]{0,1400}?\n        \}/)![0];
  assert.match(SOLVER, /if \(e\?\.nothingFilmed && hasInteracted\)/,
    'a first round that cannot film would be swallowed, hiding a widget that never films');
  assert.match(branch, /staleElementRetries < \(cfg\.maxStaleElementRetries \?\? 3\)/,
    'the re-detect is unbounded, which loops forever on a widget that never screenshots again');
});

test('a genuine animated dead end is still a failure', () => {
  // The narrow case must not swallow the broad one: a board that never settles and cannot be
  // solved from keyframes has to stay a failure.
  assert.match(SOLVER, /if \(e\?\.animated\) throw new Error\(`Animated challenge could not be solved/,
    'the animated dead end no longer ends the solve');
  const nothingAt = SOLVER.indexOf('if (e?.nothingFilmed');
  const animatedAt = SOLVER.indexOf('if (e?.animated) throw new Error(`Animated challenge');
  assert.ok(nothingAt > -1 && nothingAt < animatedAt,
    'the broad throw is reached first, so the narrow recovery never runs');
});
