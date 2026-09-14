// Literals here match page_solver.py::_solve_slide_geometry; the ratio is assumed 1:1 unless two probes prove otherwise,
// which is true of every vendor observed.
export function solveSlideGeometry(
  widths: Array<[number, number]>,
  widgetWidth: number,
): { pieceWidth: number | null; ratio: number } {
  if (!widths.length) return { pieceWidth: null, ratio: 1 };

  let ratio = 1;
  let pieceWidth: number | null = null;

  // The widest-apart pair, not the last two: 1px rounding between neighbouring probes once gave a ratio of 0.5.
  const sorted = [...widths].sort((a, b) => a[0] - b[0]);
  const [o1, w1] = sorted[0];
  const [o2, w2] = sorted[sorted.length - 1];
  if (o2 - o1 >= MIN_OFFSET_SPREAD_PX) {
    const candidate = (w2 - w1) / (o2 - o1);
    if (candidate >= MIN_RATIO && candidate <= MAX_RATIO) {
      ratio = candidate;
      pieceWidth = w1 - ratio * o1;
    }
  }

  if (pieceWidth === null) {
    const [o, w] = widths[widths.length - 1];
    pieceWidth = w - ratio * o;
  }

  if (pieceWidth < MIN_PIECE_PX || pieceWidth > widgetWidth * MAX_PIECE_FRACTION) {
    return { pieceWidth: null, ratio };
  }
  return { pieceWidth, ratio };
}

// Box widths are ±1px, so the ratio error is ±2/spread: ±0.13 at 16px against ±0.5 at 4px, multiplied into every correction.
const MIN_OFFSET_SPREAD_PX = 16.0;
// A ratio of 0.02 from a redraw sends the handle off the track and, under camoufox, into a mouse move that never returns.
const MIN_RATIO = 0.2;
const MAX_RATIO = 3.0;

export const MIN_PIECE_PX = 3.0;
export const MAX_PIECE_FRACTION = 0.6;
