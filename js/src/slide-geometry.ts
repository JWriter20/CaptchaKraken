/**
 * The algebra behind a puzzle-piece slider's closed loop.
 *
 * Split out of solver.ts because it is the one part of this feature that is
 * pure arithmetic, duplicated across the two ports, and impossible to notice
 * drifting: a wrong ratio here does not throw, it just releases the piece in the
 * wrong place, and the puzzle fails the way an unsolved puzzle fails. Keeping it
 * dependency-free means it can be unit-tested with `node --test` alongside
 * limits.ts, against the same cases as
 * `PageSolver._solve_slide_geometry` in the Python port.
 */

/**
 * Piece width and handle-to-piece travel ratio, from probe measurements.
 *
 * Each measurement is `[handle offset, width of what changed]`, where the width
 * spans the piece's ORIGINAL left edge to its CURRENT right edge — so
 * `width = pieceWidth + ratio x offset`. Two of them determine both unknowns.
 *
 * With only one usable measurement the system is underdetermined, so `ratio` is
 * ASSUMED to be 1 — true of every vendor observed, and the assumption is stated
 * here rather than buried as a default. A ratio solved from implausible
 * measurements (a redraw, a piece that hit the wall between probes) is rejected
 * the same way: better a 1:1 guess that overshoots and gets corrected than a
 * ratio of 0.02 that sends the handle off the track and, under camoufox, into a
 * mouse move that never returns.
 *
 * `pieceWidth: null` means "could not measure" — the caller falls back to
 * steering by the handle's own travel.
 */
export function solveSlideGeometry(
  widths: Array<[number, number]>,
  widgetWidth: number,
): { pieceWidth: number | null; ratio: number } {
  if (!widths.length) return { pieceWidth: null, ratio: 1 };

  let ratio = 1;
  let pieceWidth: number | null = null;

  if (widths.length >= 2) {
    const [o1, w1] = widths[0];
    const [o2, w2] = widths[widths.length - 1];
    if (o2 !== o1) {
      const candidate = (w2 - w1) / (o2 - o1);
      if (candidate >= MIN_RATIO && candidate <= MAX_RATIO) {
        ratio = candidate;
        pieceWidth = w1 - ratio * o1;
      }
    }
  }

  if (pieceWidth === null) {
    const [o, w] = widths[widths.length - 1];
    pieceWidth = w - ratio * o;
  }

  // A piece narrower than a few pixels, or wider than half the widget, is a
  // measurement of something else — a wholesale redraw, a fade, a spinner.
  if (pieceWidth < MIN_PIECE_PX || pieceWidth > widgetWidth * MAX_PIECE_FRACTION) {
    return { pieceWidth: null, ratio };
  }
  return { pieceWidth, ratio };
}

// Keep these identical to the literals in page_solver.py::_solve_slide_geometry.
const MIN_RATIO = 0.2;
const MAX_RATIO = 3.0;
const MIN_PIECE_PX = 3.0;
const MAX_PIECE_FRACTION = 0.6;
