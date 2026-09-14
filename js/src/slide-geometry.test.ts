/**
 * Cross-port parity tests for the slider's control algebra.
 *
 * These are the SAME cases as `TestSlideGeometry` in
 * python/tests/test_page_solver.py, deliberately. The two ports have to behave
 * identically (CLAUDE.md 1c), and this is the piece of the slider feature where
 * a divergence is invisible: nothing throws, the handle just stops in a
 * different place on one port than the other, and both look like an ordinary
 * unsolved puzzle.
 *
 * `node --test` rather than a framework, matching limits.test.ts — no runner, no
 * config, no dependency.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { solveSlideGeometry } from './slide-geometry';

const round6 = (n: number) => Math.round(n * 1e6) / 1e6;

test('two probes recover both unknowns', () => {
  // A 40px piece that follows the handle 1:1.
  const { pieceWidth, ratio } = solveSlideGeometry([[24, 64], [64, 104]], 400);
  assert.equal(round6(pieceWidth as number), 40);
  assert.equal(round6(ratio), 1);
});

test('a geared slider is measured, not assumed', () => {
  // Tencent-style: the piece moves further than the handle. Assuming 1:1 here
  // would stop the piece short of the gap every time.
  const { pieceWidth, ratio } = solveSlideGeometry([[20, 70], [60, 150]], 400);
  assert.equal(round6(pieceWidth as number), 30);
  assert.equal(round6(ratio), 2);
});

test('one probe falls back to a stated one-to-one', () => {
  const { pieceWidth, ratio } = solveSlideGeometry([[24, 64]], 400);
  assert.equal(pieceWidth, 40);
  assert.equal(ratio, 1);
});

test('an absurd ratio is rejected rather than steered by', () => {
  // A redraw between probes makes the two widths unrelated. A ratio of ~0.02
  // solved from that would demand a handle offset of thousands of pixels — off
  // the track, into the page, and on camoufox a mouse move that never returns.
  const { ratio } = solveSlideGeometry([[24, 64], [64, 65]], 400);
  assert.equal(ratio, 1);
});

test('a piece wider than the widget is not a piece', () => {
  const { pieceWidth } = solveSlideGeometry([[24, 390], [64, 430]], 400);
  assert.equal(pieceWidth, null);
});

test('no measurements at all is reported as such', () => {
  assert.deepEqual(solveSlideGeometry([], 400), { pieceWidth: null, ratio: 1 });
});

test('two readings taken close together do not set the ratio', () => {
  // The loop's offsets are its own corrections now, and a correction can be a
  // couple of pixels. changed_bbox answers in whole pixels, so a 1px rounding
  // across a 2px spread solves to ratio 0.5 — and every correction after it
  // would be doubled, on a widget that was already nearly home.
  const { pieceWidth, ratio } = solveSlideGeometry([[110, 150], [112, 151]], 400);
  assert.equal(ratio, 1);
  assert.equal(pieceWidth, 39);
});

test('the widest pair is used, not the two that arrived last', () => {
  // A correction can step BACK towards the handle, so the last two readings are
  // not necessarily the far-apart ones. In arrival order these last two are 2px
  // apart and their 1px of rounding reads as ratio 0.5; the widest pair spans
  // 82px and recovers the real 1:1 widget.
  const { pieceWidth, ratio } = solveSlideGeometry([[110, 150], [30, 70], [112, 151]], 400);
  assert.equal(Number(ratio.toFixed(2)), 0.99);
  assert.equal(Number(pieceWidth!.toFixed(1)), 40.4);
});
