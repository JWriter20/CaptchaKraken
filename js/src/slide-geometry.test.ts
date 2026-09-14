import { test } from 'node:test';
import assert from 'node:assert/strict';

import { solveSlideGeometry } from './slide-geometry';

const round6 = (n: number) => Math.round(n * 1e6) / 1e6;

test('two probes recover both unknowns', () => {
  const { pieceWidth, ratio } = solveSlideGeometry([[24, 64], [64, 104]], 400);
  assert.equal(round6(pieceWidth as number), 40);
  assert.equal(round6(ratio), 1);
});

test('a geared slider is measured, not assumed', () => {
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
  const { pieceWidth, ratio } = solveSlideGeometry([[110, 150], [112, 151]], 400);
  assert.equal(ratio, 1);
  assert.equal(pieceWidth, 39);
});

test('the widest pair is used, not the two that arrived last', () => {
  const { pieceWidth, ratio } = solveSlideGeometry([[110, 150], [30, 70], [112, 151]], 400);
  assert.equal(Number(ratio.toFixed(2)), 0.99);
  assert.equal(Number(pieceWidth!.toFixed(1)), 40.4);
});
