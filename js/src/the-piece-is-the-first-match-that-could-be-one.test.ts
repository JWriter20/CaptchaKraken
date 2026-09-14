/**
 * The piece is the first match that could BE a piece, not the first match.
 *
 * `findControl` takes the first visible hit, which is right for a handle or a
 * submit button — they are unique — and wrong for the piece, because the tail
 * of `SLIDE_PIECE_MEASURE_SELECTORS` is deliberately generic and a vendor may
 * carry the same word on its outer container. Measured on
 * dun.163.com/trial/jigsaw, 2026-09-13: `[class*="jigsaw"]` matches TWO
 * elements and the first in document order is the widget, 320x40 on a 340px
 * board, five times too wide and never moving. Handed to the correction loop
 * it is a number that cannot change, so the loop reports itself converged and
 * releases wherever it started.
 *
 * The size bound is the FILTER, not a check after the fact, and that is the
 * whole behaviour pinned here. The Python half is already covered by
 * python/tests/test_page_solver.py; this port had the same code and no test.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

/** The board every case below measures against: 340 * 0.6 = 204px ceiling. */
const WIDGET_W = 340;

type Box = { x: number, y: number, width: number, height: number } | null;

function el(opts: { width?: number, visible?: boolean, box?: Box, throws?: boolean }) {
  return {
    isVisible: async () => opts.visible !== false,
    boundingBox: async (): Promise<Box> => {
      if (opts.throws) throw new Error('detached mid-read');
      if (opts.box !== undefined) return opts.box;
      return { x: 10, y: 20, width: opts.width ?? 61, height: 160 };
    },
  };
}

/** A scope whose `$$` answers from `mapping`, and throws for `unparsable`. */
function scope(mapping: Record<string, any[]>, unparsable: string[] = []) {
  return {
    $$: async (selector: string) => {
      if (unparsable.includes(selector)) throw new Error('bad selector');
      return mapping[selector] ?? [];
    },
  } as any;
}

function measure(sc: any, width = WIDGET_W) {
  return (new CaptchaKrakenSolver({}) as any).measurePieceBox(sc, width);
}

test('a generic selector matching the container first still finds the piece', async () => {
  // The live Yidun reading: the widget is in document order ahead of the piece.
  const box = await measure(scope({
    '[class*="jigsaw"]': [
      el({ width: 320 }),  // div.yidun … — the board itself
      el({ width: 61 }),   // img.yidun_jigsaw — the piece
    ],
  }));
  assert.equal(box?.width, 61, 'took the container instead of the piece inside it');
});

test('a box wider than the bound is refused even when it is the only match', async () => {
  assert.equal(await measure(scope({ '[class*="jigsaw"]': [el({ width: 320 })] })), null);
});

test('a box narrower than the bound is not the piece either', async () => {
  // MIN_PIECE_PX is 3: a sliver is a border, not a puzzle piece.
  assert.equal(await measure(scope({ '.geetest_slice': [el({ width: 2 })] })), null);
});

test('a selector this adapter cannot parse does not end the search', async () => {
  const box = await measure(scope(
    { '.yidun_jigsaw': [el({ width: 61 })] },
    ['.geetest_slice', '.tencent-captcha-dy__fg-item'],
  ));
  assert.equal(box?.width, 61, 'an unparsable selector stopped the list');
});

test('an invisible match is not the piece', async () => {
  const box = await measure(scope({
    '.geetest_slice': [el({ width: 80, visible: false })],
    '.yidun_jigsaw': [el({ width: 61 })],
  }));
  assert.equal(box?.width, 61);
});

test('a match the page will not give a box for is not the piece', async () => {
  const box = await measure(scope({
    '.geetest_slice': [el({ box: null }), el({ width: 0 })],
    '.yidun_jigsaw': [el({ width: 61 })],
  }));
  assert.equal(box?.width, 61, 'a null or zero-width box was taken as a measurement');
});

test('a candidate that vanishes mid-read is not the piece', async () => {
  const box = await measure(scope({
    '.geetest_slice': [el({ throws: true })],
    '.yidun_jigsaw': [el({ width: 61 })],
  }));
  assert.equal(box?.width, 61, 'a detached element ended the search');
});

test('a page with nothing piece-shaped on it says so', async () => {
  assert.equal(await measure(scope({})), null);
});

test('the first selector that yields a plausible piece wins', async () => {
  // Order matters: the vendor-named classes lead the list precisely so the
  // generic patterns are never reached on a board that names its own piece.
  const box = await measure(scope({
    '.geetest_slice': [el({ width: 80 })],
    '[class*="jigsaw"]': [el({ width: 61 })],
  }));
  assert.equal(box?.width, 80);
});
