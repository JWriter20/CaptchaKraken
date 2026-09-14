/**
 * The opening gesture of a slide is a sweep at the slot, not a calibration nudge.
 *
 * `executeSlide` used to open by probing: two small nudges of the handle, +24px
 * and +64px, purely to measure how wide the piece is and how fast it follows,
 * before the drag had started going anywhere. That is two moves and two
 * screenshots spent in front of the vendor before the gesture reads as a drag
 * at all, and it looks nothing like a person using a slider.
 *
 * A person sweeps the handle to about where the piece belongs, looks, and
 * nudges. The distance between the piece and the slot is already an estimate of
 * the travel — the piece and the handle both start flush left — so the sweep
 * costs nothing to size, and the looks that correct it measure the widget on
 * the way, which is what the probes were for.
 *
 * The Python half is `python/tests/test_page_solver.py`'s
 * `TestSlideAimsBeforeItCorrects`; per project rule 1c the two ports must make
 * the same gesture, and a divergence here throws nothing on either side — the
 * handle just stops somewhere else.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

import { CaptchaKrakenSolver } from './solver';

const WIDGET_W = 400;
const WIDGET_H = 400;
const HANDLE = { x: 120, y: 420, width: 40, height: 30 };
const ELEMENT = { x: 100, y: 100, width: WIDGET_W, height: WIDGET_H };
const START_X = HANDLE.x + HANDLE.width / 2;   // 140
const PIECE_REST = 30;                          // the piece's centre, in the widget
const PIECE_W = 40;
const TARGET_PX = 150;

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
 * Drive `executeSlide` over a 40px piece that follows the handle 1:1 and
 * return the handle offsets it actually swept to, in order.
 */
async function drive(pieceInDom: boolean): Promise<number[]> {
  const sweeps: number[] = [];
  const state = { offset: 0 };

  const handle: any = {
    boundingBox: async () => ({ ...HANDLE }),
    scrollIntoViewIfNeeded: async () => {},
    isVisible: async () => true,
  };
  const piece: any = {
    isVisible: async () => true,
    boundingBox: async () => ({
      x: ELEMENT.x + PIECE_REST + state.offset - PIECE_W / 2,
      y: 200,
      width: PIECE_W,
      height: PIECE_W,
    }),
  };
  const element: any = {
    screenshot: async ({ path: p }: { path: string }) => writePng(p, WIDGET_W, WIDGET_H),
  };
  const page: any = { mouse: { move: async () => {}, down: async () => {}, up: async () => {} } };
  const scope: any = {
    $: async (sel: string) => {
      if (sel.includes('slider') || sel.includes('btn')) return handle;
      return pieceInDom && sel.includes('slice') ? piece : null;
    },
    // The piece is looked up with `$$`, not `$` — every match of each selector,
    // so a generic pattern that hits the vendor's outer container before the
    // piece cannot win. See `measurePieceBox`.
    $$: async (sel: string) =>
      (pieceInDom && sel.includes('slice') ? [piece] : []),
  };

  const solver: any = new CaptchaKrakenSolver({});
  // The hover that grabs the handle is not a sweep and carries no piece.
  solver.move = async () => {};
  solver.performSmoothMove = async (_page: unknown, x: number) => {
    state.offset = x - START_X;
    sweeps.push(state.offset);
  };
  solver.trackPiece = async () => ({
    bbox: [
      Math.round(PIECE_REST - PIECE_W / 2), 0,
      Math.round(PIECE_REST + PIECE_W / 2 + state.offset), 20,
    ],
    piece: null,
  });

  const frac = TARGET_PX / WIDGET_W;
  await solver.executeSlide(
    page, element, scope, { target_bounding_box: [frac, 0.4, frac, 0.6] }, ELEMENT,
  );
  return sweeps;
}

test('the first move goes most of the way to the slot', async () => {
  // The handle sits 40px into the widget and the slot is at 150px, so the
  // opening sweep is the ~110px between them — not 24px.
  const sweeps = await drive(false);
  assert.ok(sweeps[0] > 100, `opened with a ${sweeps[0].toFixed(0)}px nudge, not a sweep`);
});

test('the piece the page names is where the sweep is aimed', async () => {
  // Piece centre 30 within the widget, slot at 150: 120px of travel, exactly.
  // The first look only confirms it, so there is nothing to correct.
  const sweeps = await drive(true);
  assert.deepEqual(sweeps, [120], `expected one exact sweep, got ${JSON.stringify(sweeps)}`);
});

/**
 * MEASURED on a Tencent slide board: the handle's centre sits 42px into a 360px
 * widget and the piece's sits 136px in, so aiming the HANDLE at a slot 288px
 * across sends the PIECE to 382 — off the right edge, where the only thing left
 * to photograph is the ground it vacated. Reported as "nothing moved" that is
 * unrecoverable; reported as the ghost plus the travel, the next correction
 * brings it back. Mirror of `TestASweepThatOvershootsTheBoard` in
 * python/tests/test_page_solver.py.
 */
const OVER = { widget: 360, pieceRest: 136, pieceW: 42, startX: 42, target: 288.4 };

async function driveOvershoot(): Promise<{ sweeps: number[], finalCentre: number }> {
  const sweeps: number[] = [];
  const state = { offset: 0 };
  const handle: any = {
    boundingBox: async () => ({ x: 15, y: 297, width: 54, height: 28 }),
    scrollIntoViewIfNeeded: async () => {},
    isVisible: async () => true,
  };
  const element: any = {
    screenshot: async ({ path: p }: { path: string }) =>
      writePng(p, OVER.widget, OVER.widget),
  };
  const page: any = { mouse: { move: async () => {}, down: async () => {}, up: async () => {} } };
  const scope: any = { $: async (sel: string) => (sel.includes('slider') ? handle : null) };

  const solver: any = new CaptchaKrakenSolver({});
  solver.move = async () => {};
  solver.performSmoothMove = async (_p: unknown, x: number) => {
    state.offset = x - OVER.startX;
    sweeps.push(state.offset);
  };
  // What locate_piece answers for this widget, 1:1.
  solver.trackPiece = async (_e: unknown, _b: string, _a: string, _x: number[], travel = 0) => {
    const restLeft = OVER.pieceRest - OVER.pieceW / 2;
    const centre = OVER.pieceRest + state.offset;
    const right = centre + OVER.pieceW / 2;
    if (right > OVER.widget) {
      return {
        bbox: [restLeft, 0, restLeft + OVER.pieceW, 20],
        piece: { centre: OVER.pieceRest + travel, width: OVER.pieceW },
      };
    }
    return { bbox: [restLeft, 0, right, 20], piece: { centre, width: OVER.pieceW } };
  };

  const frac = OVER.target / OVER.widget;
  await solver.executeSlide(
    page, element, scope, { target_bounding_box: [frac, 0.4, frac, 0.6] },
    { x: 0, y: 0, width: OVER.widget, height: OVER.widget },
  );
  return { sweeps, finalCentre: OVER.pieceRest + state.offset };
}

test('a sweep that puts the piece off the board is brought back', async () => {
  const { sweeps, finalCentre } = await driveOvershoot();
  assert.ok(
    sweeps[0] > OVER.widget - OVER.pieceRest,
    `first sweep ${sweeps[0]} does not overshoot; this test is about nothing`,
  );
  assert.ok(sweeps.length > 1, 'released at the overshoot without correcting');
  assert.ok(
    Math.abs(finalCentre - OVER.target) <= 2,
    `piece released at ${finalCentre.toFixed(1)}px, wanted ${OVER.target}`,
  );
});

test('a sweep that lands short is corrected from the screen', async () => {
  // With no piece element the sweep is aimed from the HANDLE, so it lands short
  // by however far the piece sits from it. That is what the looks are for.
  const sweeps = await drive(false);
  assert.ok(sweeps.length > 1, 'no correction after an estimate that was off');
  assert.ok(
    Math.abs(sweeps[sweeps.length - 1] - 120) <= 2,
    `released at ${sweeps[sweeps.length - 1]}px of travel, wanted 120`,
  );
});
