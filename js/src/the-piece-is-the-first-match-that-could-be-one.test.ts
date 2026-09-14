import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

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
  const box = await measure(scope({
    '[class*="jigsaw"]': [
      el({ width: 320 }),
      el({ width: 61 }),
    ],
  }));
  assert.equal(box?.width, 61, 'took the container instead of the piece inside it');
});

test('a box wider than the bound is refused even when it is the only match', async () => {
  assert.equal(await measure(scope({ '[class*="jigsaw"]': [el({ width: 320 })] })), null);
});

test('a box narrower than the bound is not the piece either', async () => {
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
  const box = await measure(scope({
    '.geetest_slice': [el({ width: 80 })],
    '[class*="jigsaw"]': [el({ width: 61 })],
  }));
  assert.equal(box?.width, 80);
});
