/**
 * Where the piece IS, asked of the page — not inferred from a pixel diff.
 *
 * The CV fallback measures the piece as the union of everything that changed
 * between two frames: the ground it vacated plus where it is now. Its edges are
 * inset by however much the piece fades into the board, and that inset cancels
 * out of the RATIO but not out of the WIDTH. `pieceCentre = rightEdge - width/2`
 * then turns an under-measured width into a centre that is too far RIGHT by
 * half the error — so the loop reports itself converged while the piece is
 * short of the notch.
 *
 * MEASURED on gt4.geetest.com's slide demo, 2026-09-12:
 *
 *   .geetest_slice is 80x80 in the DOM
 *   the diff inferred 63px and 74px on two consecutive live attempts
 *   63 puts the centre 8.5px right of truth -> releases 8.5px short
 *   8.5px on a 340px widget is 2.5%; the notch accepts about 2%
 *
 * Across two filmed runs of ten attempts, every one of the fourteen refused
 * drags undershot and not one overshot — the signature of a bias, not of noise.
 *
 * The Python half is python/tests/test_slide_reads_the_piece_off_the_page.py.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

const BOX = { x: 0, y: 0, width: 340, height: 400 };
/** The notch centre the model asked for, in element CSS px. */
const TARGET = 245;
const TRUE_PIECE_W = 80;
/** What the diff under-measures it as — the live reading from the docstring. */
const DIFF_PIECE_W = 63;

/**
 * Drive `executeSlide` against a widget whose piece really is TRUE_PIECE_W wide
 * and moves one-for-one with the handle, while `trackPiece` reports the
 * narrower union the real CV returns. Returns where the pointer ended up.
 */
async function runSlide({ pieceInDom }: { pieceInDom: boolean }) {
  const s: any = new CaptchaKrakenSolver({});
  const moves: number[] = [];
  let handleX = 40;              // where the handle starts, page coords
  const pieceStartCentre = TRUE_PIECE_W / 2;

  const pieceCentreNow = () => pieceStartCentre + (handleX - 40);

  s.findControl = async (_scope: any, sels: ReadonlyArray<string>) => {
    if (sels.some((x) => x.includes('slider') || x.includes('btn') || x.includes('handle'))) {
      return { boundingBox: async () => ({ x: handleX - 25, y: 300, width: 50, height: 40 }) };
    }
    if (!pieceInDom) return null;
    return {
      boundingBox: async () => ({
        x: pieceCentreNow() - TRUE_PIECE_W / 2, y: 100,
        width: TRUE_PIECE_W, height: TRUE_PIECE_W,
      }),
    };
  };
  // The piece is measured through `measurePieceBox`, which takes EVERY match of
  // each selector and keeps the first that could be a piece — `findControl`'s
  // first-visible-hit is right for the handle and wrong for the piece.
  s.measurePieceBox = async () => (pieceInDom ? {
    x: pieceCentreNow() - TRUE_PIECE_W / 2, y: 100,
    width: TRUE_PIECE_W, height: TRUE_PIECE_W,
  } : null);
  s.shotScale = () => 1;
  s.move = async () => {};
  s.performSmoothMove = async (_p: any, x: number) => { handleX = x; moves.push(x); };
  s.human = { press: async () => {}, release: async () => {}, pause: async () => {}, drag: async () => {} };
  /*
   * The union the real diff returns, with the asymmetry the live probes imply.
   *
   * The measured pairs were [24 -> 86px] and [64 -> 125px], i.e. width =
   * 63 + travel against a piece that is really 80 wide. Solving that: the
   * piece's CURRENT RIGHT edge reads sharp (it meets fresh board), and the
   * VACATED LEFT edge is inset by the full 17px (the ground it left behind
   * still resembles the piece that was on it). That asymmetry is the whole
   * defect — a symmetric inset would cancel out of `right - width/2`.
   */
  const LEFT_INSET = TRUE_PIECE_W - DIFF_PIECE_W;
  s.trackPiece = async () => {
    const origLeft = pieceStartCentre - TRUE_PIECE_W / 2;
    // `piece: null`: this is the vendor whose piece the CV cannot separate from
    // the ground it vacated, which is the whole point of the test.
    return {
      bbox: [origLeft + LEFT_INSET, 0, pieceCentreNow() + TRUE_PIECE_W / 2, TRUE_PIECE_W],
      piece: null,
    };
  };

  const element = { screenshot: async () => {} };
  await s.executeSlide(
    {} as any, element as any, {} as any,
    { action: 'drag', target_bounding_box: [TARGET / BOX.width, 0.4, TARGET / BOX.width, 0.5] } as any,
    BOX,
  );
  return { finalPieceCentre: pieceCentreNow(), moves };
}

test('with the piece in the DOM the drag lands on the notch', async () => {
  const { finalPieceCentre } = await runSlide({ pieceInDom: true });
  const err = finalPieceCentre - TARGET;
  assert.ok(
    Math.abs(err) <= 2,
    `should seat within the notch, landed ${err.toFixed(1)}px off`,
  );
});

test('the diff-only path is what undershoots, and is still the fallback', async () => {
  // Not an aspiration — this is the behaviour measured live, reproduced here so
  // the fix above is pinned against the thing it fixes. A vendor that draws its
  // piece into a canvas still gets this path, and still solves; it is simply
  // less accurate, which is why the DOM reading is preferred when it exists.
  const { finalPieceCentre } = await runSlide({ pieceInDom: false });
  const err = finalPieceCentre - TARGET;
  assert.ok(err < -4, `expected the known undershoot, got ${err.toFixed(1)}px`);
  assert.ok(
    Math.abs(err - -(TRUE_PIECE_W - DIFF_PIECE_W) / 2) < 1.5,
    `the undershoot should be half the width error (${(TRUE_PIECE_W - DIFF_PIECE_W) / 2}px), got ${(-err).toFixed(1)}px`,
  );
});
