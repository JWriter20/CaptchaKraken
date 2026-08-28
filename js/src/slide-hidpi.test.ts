/**
 * Regression, 2026-08-27: the slider missed every attempt on a phone.
 *
 * `executeSlide` closes a loop between two pixel spaces. It steers the handle
 * in CSS pixels — `boundingBox()`, the probe offsets, the model's slot — and it
 * MEASURES the piece in the screenshot's pixels, which is where `changed_bbox`
 * masks the handle and reports what moved. Those are the same number on a 1x
 * desktop, so the loop was correct for a year and the two spaces were never
 * told apart.
 *
 * Tier 3's mobile arm drives a Pixel 7, device-pixel ratio 2.625. There the
 * mask landed on a strip of empty card ABOVE the handle, leaving the handle
 * itself the largest moving thing in frame; the widths came back 2.6x too wide;
 * `solveSlideGeometry` threw them out as wider than the widget; and the drive
 * fell back to open-loop guessing. Measured on geetest_v3_slide: mouse arm 2/3
 * boards solved, mobile arm 1/3 (js) and 0/3 (python), on the same three seeds.
 *
 * The Python driver had the identical bug and is fixed in the same commit;
 * `python/tests/test_page_solver.py::TestSlideDriver` pins that half. Per
 * CLAUDE.md 1c the two ports must behave the same, and this is a divergence
 * that would throw nothing on either — the handle just stops somewhere else.
 *
 * A fake page rather than a browser: what is under test is the arithmetic
 * between a bounding box and an image, and neither needs Firefox.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

import { CaptchaKrakenSolver } from './solver';

const DPR = 2.625;          // Pixel 7
const WIDGET_W = 400;
const WIDGET_H = 400;
const PIECE_LEFT = 10;
const PIECE_W = 40;
const HANDLE = { x: 120, y: 420, width: 40, height: 30 };
const ELEMENT = { x: 100, y: 100, width: WIDGET_W, height: WIDGET_H };

/** A byte-valid PNG header — enough for readPngDimensions and file I/O. */
function writePng(file: string, width: number, height: number): void {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; ihdr[9] = 2;
  const len = Buffer.alloc(4); len.writeUInt32BE(13, 0);
  fs.writeFileSync(file, Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    len, Buffer.from('IHDR'), ihdr, Buffer.alloc(4),
  ]));
}

/**
 * The simulated widget: a 40px piece starting 10px from the left, carried 1:1
 * by the handle, on a screen with `dpr` device pixels per CSS pixel. The shots
 * are written at the device size and `trackPiece` answers in device pixels,
 * exactly as the real pair does.
 */
function rig(targetPx: number, dpr: number) {
  const moves: Array<[number, number]> = [];
  const excludes: number[][] = [];
  const startX = HANDLE.x + HANDLE.width / 2;

  const handle: any = {
    boundingBox: async () => ({ ...HANDLE }),
    scrollIntoViewIfNeeded: async () => {},
    isVisible: async () => true,
  };
  const element: any = {
    screenshot: async ({ path: p }: { path: string }) =>
      writePng(p, Math.round(WIDGET_W * dpr), Math.round(WIDGET_H * dpr)),
  };
  const page: any = {
    mouse: {
      move: async (x: number, y: number) => { moves.push([x, y]); },
      down: async () => {},
      up: async () => {},
    },
  };
  const scope: any = { $: async (sel: string) => (sel.includes('slider') ? handle : null) };

  const solver: any = new CaptchaKrakenSolver({});
  solver.trackPiece = async (
    _el: unknown, _before: string, _after: string, exclude: number[],
  ) => {
    excludes.push([...exclude]);
    const offset = moves[moves.length - 1][0] - startX;
    const right = PIECE_LEFT + PIECE_W + offset;
    return [Math.round(PIECE_LEFT * dpr), 0, Math.round(right * dpr), Math.round(20 * dpr)];
  };

  const frac = targetPx / WIDGET_W;
  const action = { target_bounding_box: [frac, 0.4, frac, 0.6] };
  return { solver, page, element, scope, action, moves, excludes, startX };
}

test('a hidpi screen still lands the piece on the slot', async () => {
  // piece centre = PIECE_LEFT + PIECE_W/2 + offset = 30 + offset, so a slot at
  // 150px within the widget wants the handle 120px along.
  const r = rig(150, DPR);
  await r.solver.executeSlide(r.page, r.element, r.scope, r.action, ELEMENT);
  const releasedAt = r.moves[r.moves.length - 1][0];
  assert.ok(
    Math.abs((releasedAt - r.startX) - 120) <= 2,
    `released at ${releasedAt - r.startX}px of travel, wanted 120`,
  );
});

test("the handle's mask is in the shot's pixel space", async () => {
  const r = rig(150, DPR);
  await r.solver.executeSlide(r.page, r.element, r.scope, r.action, ELEMENT);
  // The handle's band, in the shot's pixels: y 420..450 in page space is
  // 320..350 within the element, and the mask spans the full widget width.
  const [x1, y1, x2, y2] = r.excludes[0];
  assert.equal(x1, 0);
  assert.ok(Math.abs(x2 - WIDGET_W * DPR) <= 1, `mask spans ${x2}, wanted ${WIDGET_W * DPR}`);
  assert.ok(y1 <= 320 * DPR && y2 >= 350 * DPR, `mask covers ${y1}..${y2}, not the handle`);
});

test('a 1x screen is unchanged', async () => {
  const r = rig(150, 1);
  await r.solver.executeSlide(r.page, r.element, r.scope, r.action, ELEMENT);
  const releasedAt = r.moves[r.moves.length - 1][0];
  assert.ok(Math.abs((releasedAt - r.startX) - 120) <= 2);
  assert.deepEqual(r.excludes[0].map(Math.round), [0, 310, 400, 361]);
});

// The rig writes into the real tmpdir through the driver's own shot paths;
// nothing else to clean up — executeSlide unlinks them itself.
void path; void os;
