import cv2
import numpy as np
import os
import tempfile
from dataclasses import dataclass
from typing import List, Tuple, Optional
from ..overlay import add_overlays_to_image
from ..image_processor import ImageProcessor

# =============================================================================
# Grid detection: adaptive consistent-color line tracer.
#
# Detects 3x3 / 4x4 captcha grids of ANY uniform separator colour and small tilt
# by tracing each gutter pixel-by-pixel (consistent-colour walk + perpendicular
# slant re-find), clustering duplicate traces, then forming an evenly-spaced
# lattice. The decisive false-positive gate is CELL DIVERGENCE: a real grid's
# cells are filled with content distinct from the gutter colour, whereas a flat
# region (white wall, sky, watermark haze) has "cells" the same colour as its
# "gutters". Ported from the grid_tracer dev harness; see that harness + the
# project memory note `project_find_grid_tracer_*` for the iteration history.
# =============================================================================


# ── Tuning constants ────────────────────────────────────────────────────────
COLOR_TOL = 10.0       # LAB ΔE: same-color test for ridge build + perp thickness
CONT_TOL = 14.0        # LAB ΔE: LOCAL continuation test vs the running line mean
                       # — tolerates JPEG / anti-alias / lighting jitter along a
                       # real gutter. A real tile edge (ΔE >> this) ends the line.
CONT_TOL2 = CONT_TOL * CONT_TOL   # squared, for the hot continuation test
SEED_L_TOL = 6.0       # LAB L: max deviation of ANY line pixel's LIGHTNESS from
                       # the seed's. L is the stable, perceptually-dominant signal:
                       # a real gutter's L span is ~3 across its length, while a
                       # shaded surface (grass/water) drifts ~32. We gate on L (not
                       # full ΔE) because a/b are JPEG chroma noise — visually
                       # irrelevant at gutter lightness, but CIE76 ΔE over-weights
                       # them and wrongly dropped real tinted-grey gutters.
STEP_L_TOL = 4.0       # LAB L: max lightness change between CONSECUTIVE pixels.
                       # A gutter never jumps (consecutive |ΔL| <= ~2); grass
                       # jumps up to ~16. Stops a line crossing a tile edge whose
                       # far side happens to share the seed's lightness band.
MIN_RUN = 10           # px: min consecutive same-color pixels to seed a line
MAX_THICKNESS = 14     # px: a separator thicker than this is a band, not a line
                       # (no longer a hard reject in the perp walk — see PERP_SCAN)
PERP_SCAN = 60         # px: half-width of the perpendicular strip the thickness
                       # walk searches. Wide enough to reach the boundary when a
                       # gutter is fused with same-colour tile content, instead of
                       # truncating at +/-MAX_THICKNESS and rejecting the gutter.
PERP_REFIND = 3        # px: when the along-walk hits a cell, look this far
                       # perpendicular (up then down) for the gutter colour. Found
                       # -> the gutter slanted away, step there and continue. Not
                       # found -> a genuine wall, stop. (Replaces thickness/midpoint
                       # band re-centering entirely.) ~3px tolerates ~25deg tilt.
MAX_PERP_JUMP = 6.0    # px: max perpendicular re-center per save (bounds drift)
MERGE_PX = 8.0         # px: cluster lines whose midline positions are this close
MERGE_ANGLE = 0.07     # rad (~4°): and whose angles are this close
MIN_CELL = 36          # px: minimum cell pitch (cells have a minimum size)
LINE_STD_TOL = 7.0     # LAB: max std of color along a line (consistency gate)
STEP = 1               # px: walk one pixel at a time (cheap: just a color test
                       # per step; perpendicular thickness only computed on save)
SLANT_CAP = 0.47       # tan(~25°): reject lines tilted beyond the stated max
SUPPORT_FRAC = 0.42    # traced length must be >= this fraction of the span
TERM_FRAC = 0.30       # >= this fraction of traced points must terminate both sides
MAX_SEED_FRAC = 0.34   # seed rows/cols only within +/- this fraction of center.
                       # A grid gutter spans the FULL image, so it is hit by seeds
                       # from a central band; the trace then walks outward to the
                       # full extent on its own. Narrowing the band (from 0.46) skips
                       # the content seeds near the top/bottom margins that almost
                       # always die, cutting per-image trace attempts ~25% with no
                       # loss of detection (every gutter still seeded many times for
                       # the cluster-average).
EDGE_MARGIN = 0.02     # drop lines within this fraction of an image edge
# Stage-C structural gates (the primary negative-rejection — a real grid's
# internal separators are one colour, one thickness, one coherent tilt):
GRID_COLOR_TOL = 8.0   # LAB ΔE: max spread of chosen separators' colours
GRID_THICK_TOL = 4.0   # px: max thickness spread across chosen separators
GRID_ANGLE_TOL = 0.09  # rad (~5°): H tilt must ≈ -V tilt for a coherent grid
XAXIS_COLOR_TOL = 6.0  # LAB ΔE: H-mean gutter colour must ≈ V-mean (real grids
                       # share ONE gutter colour across both axes; photo "grids"
                       # pair an H edge of one colour with a V edge of another)
LATTICE_TOL = 0.18     # a same-colour line within this fraction of a pitch of a
                       # lattice node counts as on-grid (a border line), not noise
MAX_OFF_LATTICE = 1    # max same-colour internal lines allowed OFF the lattice
                       # before the structure is judged photo texture, not a grid
EVEN_TOL = 0.18        # consecutive cell gaps must match the median pitch within
                       # this fraction (the equidistant-cells rule)
MIN_GRID_DIM = 3       # min cells per axis (currently >=3x3; rectangles like 6x4
                       # are allowed — rows and cols may differ, each >= this)
CORROB_FRAC = 0.9      # frac of a cell a perpendicular line must extend PAST a
                       # candidate line (on both sides) to corroborate it as a real
                       # internal separator. Set near 1.0 (a FULL cell): a true
                       # internal line has a real cell — so the perpendicular gutters
                       # run ~a full cell — beyond it on each side. A frame/edge line
                       # fails: the perpendicular gutters reach only the grid border,
                       # LESS than a cell past it (no cell beyond the edge). This is
                       # what drops both the inset-V and full-width-H frame lines.
                       # Always treat the grid as OPEN (extrapolate outer cells).
GRID_OVERSHOOT = 0.35  # frac of pitch: the grid extrapolated one cell past each
                       # outer INTERNAL line may exceed the image edge by at most
                       # this much. Real grids bleed to the edge (no reliable outer
                       # border), so a small overshoot is expected; a large one
                       # means the chosen internal lines don't actually frame a grid.
FULL_SPAN_MARGIN = 0.5 # frac of pitch: every chosen lattice line must span at
                       # least (N - FULL_SPAN_MARGIN)*pitch end-to-end, i.e. cross
                       # essentially the WHOLE grid (all N cells), allowing only a
                       # half-cell fade/occlusion at the ends. Rejects short central
                       # edges (e.g. an object in a reference photo) masquerading as
                       # grid lines — the whole lattice must be present, not just the
                       # central closed region.
MIN_GRID_COVERAGE = 0.72  # frac: the grid's total extent (N*pitch) must cover at
                       # least this fraction of the image on each axis. A real
                       # captcha grid FILLS the frame (3x3 pitch ~ img/3 -> coverage
                       # ~1.0); the main FP mode is two object-edges crammed near the
                       # centre (small pitch -> the implied grid is a small central
                       # patch). This single ratio kills that whole class.
CELL_INSET = 0.22      # frac of a cell's side to trim off each edge before
                       # sampling the cell INTERIOR (keeps gutter pixels out of the
                       # cell-content mean).
CELL_DIVERGE_TOL = 12.0   # LAB ΔE: a cell's interior mean must differ from the
                       # gutter colour by at least this much to count as a real
                       # (content-filled) cell. Real captcha cells hold photo/object
                       # content -> large ΔE; a flat region (white wall, watermark
                       # haze, sky) has cells the same colour as its "gutters".
CELL_DIVERGE_FRAC = 0.6   # frac of cells that must diverge (>CELL_DIVERGE_TOL) from
                       # the gutter. A real grid: most cells are filled. A flat-image
                       # FP: few/none diverge. Robust to a couple of genuinely light
                       # cells (e.g. an all-sky tile) in an otherwise real grid.
CLEAN_GUTTER_STD = 2.3    # LAB: mean color_std of the chosen separators below which
                       # the gutters are judged PAINTED (a real captcha grid), not
                       # texture. Measured: real-grid gutters have color_std ~0-2
                       # (a uniform drawn line); textured-photo pseudo-gutters
                       # (grass/foliage/tessellated bg) run ~3-6. This is a content-
                       # INDEPENDENT grid signal, so when it holds we can trust the
                       # lattice even if several cells are pale (sky / faint sketches).
CELL_DIVERGE_FRAC_CLEAN = 0.42  # relaxed content frac used ONLY when the gutters are
                       # clean (< CLEAN_GUTTER_STD). A painted-gutter grid with sky /
                       # pale-sketch tiles legitimately has fewer content-bearing
                       # cells; the clean gutter already proves it is a grid, so we
                       # do not also demand a content majority. FPs keep the strict
                       # 0.6 frac because their pseudo-gutters are noisy (std >= 2.8),
                       # well above CLEAN_GUTTER_STD — they never qualify for relaxation.
SEED_RUN_FRAC = 0.5    # frac of the along-scan width a seed's ridge run must
                       # cover to be walked. A grid gutter spans the scan (>=0.88
                       # measured); content fragments are short (p90 ~0.35). This
                       # pre-filter drops ~95% of dead seeds (which were ~73% of all
                       # walk steps) before the walk, with no loss of real gutters.
SEED_DECIMATE = 2      # seed every 2nd row/col: robust multi-position seeding
                       # within a run still catches 2px-tall gutters, at ~half cost
# Lattice completion (recovers grids whose internal gutter between two same-colour
# tiles — sky/horizon — could not be traced, so a row/col is missing). Only fires
# on CLEAN painted anchors; the content gate still decides FPs.
CLEAN_LATTICE_STD = 2.0   # LAB: an anchor line used for lattice completion must be
                       # at least this uniform. Real painted gutters read std ~0-1.5;
                       # textured photo edges run >= 2.8, so they never anchor a
                       # completed lattice (matches CLEAN_GUTTER_STD's intent).
MAX_VIRTUAL_FRAC = 0.5    # at most this fraction of a completed lattice's internal
                       # nodes may be EXTRAPOLATED/INTERPOLATED (the rest must be real
                       # clean lines). A 4-cell grid (3 internal lines) may invent 1;
                       # we never reconstruct a grid that is mostly invented.
MAX_VIRTUAL_NODES = 2     # hard cap on invented internal lines per completed run.
                       # Recovers a single missing internal gutter (the common
                       # sky-bordered-row case) and at most one outer-line extension;
                       # prevents conjuring a whole grid from a 2-line fragment.
VIRTUAL_NODE_PENALTY = 600.0  # score added per invented node, so a fully-real
                       # lattice of the same dimension always outranks a completed
                       # one and fewer interpolations win.
SPAN_FIT_PENALTY = 900.0  # score added per WHOLE UNCOVERED CELL the perpendicular
                       # gutters run past a grid border (see the grid-span-fit block).
                       # Outweighs the virtual-node penalty so a completed 4x4 beats
                       # the 3-row subset whose gutters run a cell past the border.
MISSING_LINE_FRAC = 0.8   # frac of a pitch: a gutter must overshoot a grid border by
                       # at least this much to count as an uncovered (missing) row/col.
                       # A real grid's gutters can BLEED a little past the frame (into
                       # a submit bar / header — hcaptcha overshoots ~0.65 cell) without
                       # implying another cell; only a near-full-cell overshoot does.
EDGE_BLEED_PX = 6      # px: if a gutter's traced span reaches this close to the image
                       # edge, an overshoot on that side is gutter-colour bleeding into
                       # a margin / white footer / header (which touches the edge), not
                       # a real extra cell — so it does NOT trigger a missing row/col.
                       # Real captcha grids are inset from the edges (the gutters stop
                       # ~60-120px short), so this never suppresses a true missing line.
MAX_OFF_LATTICE_CLEAN = 1   # max stray CLEAN off-lattice lines forgiven on a proven
                       # clean grid (a horizon / wire / UI rule). Beyond this the
                       # strays are counted — a textured photo whose white-ish edges
                       # are clean produces several, so the off-lattice FP gate still
                       # fires; a real sky-bordered grid has at most one or two.
OFF_LATTICE_CLEAN_STD = 2.6   # in the off-lattice FP gate, a same-colour internal
                       # line this clean that lies OFF the chosen pitch is treated as
                       # a stray painted edge (sky horizon / UI rule), not proof the
                       # cells are irregular: it is NOT counted. Noisy pseudo-gutters
                       # (std above this) still count, so the textured-photo FP gate
                       # is unchanged. Lets a correct lattice survive a couple of
                       # spurious clean full-span lines (horizon, power line).


@dataclass
class PotentialGridLine:
    orientation: str
    angle: float
    thickness: float
    start: Tuple[float, float]
    end: Tuple[float, float]
    color_lab: np.ndarray
    color_std: float
    midline_pos: float
    support: int


def _de(a, b):
    d = a - b
    return float(np.sqrt(np.dot(d, d)))


def _de2(a, b):
    """Squared LAB distance — avoids the sqrt in hot comparison loops."""
    d0 = a[0] - b[0]; d1 = a[1] - b[1]; d2 = a[2] - b[2]
    return d0 * d0 + d1 * d1 + d2 * d2


def _line_extent(line):
    """Endpoint-to-endpoint span of a fitted line along its PRIMARY axis (the
    direction it runs): H lines -> |end.x - start.x|, V lines -> |end.y - start.y|.
    This is the fit extent (how far the gutter was actually traced across the
    image), used to require that each lattice line spans the WHOLE grid, not just
    the central cell — a short edge near the centre (e.g. an object inside a
    reference photo) is not a grid line."""
    if line.orientation == 'h':
        return abs(line.end[0] - line.start[0])
    return abs(line.end[1] - line.start[1])


def _cell_divergences(lab, boxes, gutter_color):
    """For each cell box, mean LAB of its INTERIOR (shrunk inward by CELL_INSET so
    we don't sample the gutter pixels on the cell edges), and its ΔE from the
    gutter colour. Returns a list of per-cell ΔE. A REAL grid's cells are filled
    with photo/object content distinct from the gutter -> large divergence; a flat
    or near-uniform region (white wall, watermark haze, sky) yields cells the SAME
    colour as the 'gutters' -> tiny divergence (there are no real cells at all)."""
    h, w = lab.shape[:2]
    out = []
    for (x1, y1, x2, y2) in boxes:
        bw, bh = x2 - x1, y2 - y1
        ix1 = int(x1 + CELL_INSET * bw); ix2 = int(x2 - CELL_INSET * bw)
        iy1 = int(y1 + CELL_INSET * bh); iy2 = int(y2 - CELL_INSET * bh)
        ix1 = max(0, min(w - 1, ix1)); ix2 = max(ix1 + 1, min(w, ix2))
        iy1 = max(0, min(h - 1, iy1)); iy2 = max(iy1 + 1, min(h, iy2))
        patch = lab[iy1:iy2, ix1:ix2].reshape(-1, 3)
        if patch.shape[0] == 0:
            out.append(0.0); continue
        mean = patch.mean(axis=0)
        out.append(_de(mean, gutter_color))
    return out


def _cells_have_content(lab, boxes, rows, cols, gutter_lines):
    """True if a MAJORITY of cells diverge from the gutter colour (real content,
    not a flat region). This is the FP killer. We do NOT require every row/column
    to have content: a legitimate grid can have an extrapolated OUTER row/column
    that lands on page background (e.g. a reCAPTCHA 4x4 whose top row reaches above
    the photo into the header) — that is expected, not a reason to reject. Dimension
    is decided by line corroboration + the unused-lines preference, not by content
    per row/col."""
    if lab is None:
        return True
    gutter_color = np.mean([l.color_lab for l in gutter_lines], axis=0)
    divs = _cell_divergences(lab, boxes, gutter_color)
    if not divs or len(divs) != rows * cols:
        return False
    # Clean-gutter relaxation: if the chosen separators are painted-uniform
    # (mean color_std < CLEAN_GUTTER_STD) the lattice is already proven to be a
    # real grid by the gutters alone — sky / pale-sketch tiles are then allowed,
    # so we require only a smaller fraction of content-bearing cells. Textured-
    # photo pseudo-gutters (the FP mode) are noisy (std well above the threshold)
    # and stay on the strict content fraction.
    gutter_std = float(np.mean([l.color_std for l in gutter_lines]))
    frac = CELL_DIVERGE_FRAC_CLEAN if gutter_std < CLEAN_GUTTER_STD else CELL_DIVERGE_FRAC
    return sum(1 for d in divs if d > CELL_DIVERGE_TOL) >= frac * len(divs)


def _to_lab(img_bgr):
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[:, :, 0] *= (100.0 / 255.0)
    lab[:, :, 1] -= 128.0
    lab[:, :, 2] -= 128.0
    return lab


# ── Stage A: vectorized O(n*m) ridge / run-length map ───────────────────────
def _runlength(same, axis):
    """Inclusive running count of consecutive True along axis; resets on False."""
    s = same.astype(np.int32)
    out = np.zeros_like(s)
    if axis == 1:
        out[:, 0] = s[:, 0]
        for j in range(1, s.shape[1]):
            out[:, j] = (out[:, j - 1] + 1) * s[:, j]
    else:
        out[0, :] = s[0, :]
        for i in range(1, s.shape[0]):
            out[i, :] = (out[i - 1, :] + 1) * s[i, :]
    return out


def _build_ridge_map(lab, axis):
    """Boolean map: True where the pixel belongs to a consistent-color run of
    length >= MIN_RUN along the axis (axis=1 horizontal, axis=0 vertical)."""
    h, w = lab.shape[:2]
    if axis == 1:
        diff = lab[:, 1:, :] - lab[:, :-1, :]
        de = np.sqrt(np.sum(diff * diff, axis=2))
        same = np.concatenate([np.zeros((h, 1), bool), de < COLOR_TOL], axis=1)
        run = _runlength(same, 1)
        end_ok = run >= (MIN_RUN - 1)
        ridge = end_ok.copy()
        for j in range(w - 2, -1, -1):
            ridge[:, j] |= ridge[:, j + 1] & same[:, j + 1]
    else:
        diff = lab[1:, :, :] - lab[:-1, :, :]
        de = np.sqrt(np.sum(diff * diff, axis=2))
        same = np.concatenate([np.zeros((1, w), bool), de < COLOR_TOL], axis=0)
        run = _runlength(same, 0)
        end_ok = run >= (MIN_RUN - 1)
        ridge = end_ok.copy()
        for i in range(h - 2, -1, -1):
            ridge[i, :] |= ridge[i + 1, :] & same[i + 1, :]
    return ridge


# ── Vectorized perpendicular thickness + centerline ─────────────────────────
def _perp_from_strip(strip, center_idx, ref, max_t):
    """`strip` is an (N,3) LAB slice perpendicular to the line, `center_idx` the
    index of the query point within it. Compute thickness + centerline by
    measuring the contiguous run of within-COLOR_TOL pixels around center_idx.
    Returns (offset_from_center, thickness, terminated_both) or None.

    Fully vectorized: one ΔE vector over the (small) strip, then run extents."""
    n = len(strip)
    if not (0 <= center_idx < n):
        return None
    diff = strip - ref
    de = np.sqrt(np.einsum('ij,ij->i', diff, diff))   # (N,)
    same = de < COLOR_TOL
    if not same[center_idx]:
        return None
    # up = consecutive same going to lower indices
    up = 0
    i = center_idx - 1
    while i >= 0 and same[i]:
        up += 1; i -= 1
    up_term = (i >= 0) and (not same[i])
    down = 0
    i = center_idx + 1
    while i < n and same[i]:
        down += 1; i += 1
    down_term = (i < n) and (not same[i])
    thick = up + down + 1
    # NOTE: the old `if thick > max_t: return None` rejection is REMOVED. A real
    # white gutter adjacent to white tile content reads as one band far thicker
    # than max_t, which wrongly rejected the gutter. `max_t` is no longer used as
    # a hard thickness gate here.
    offset = (down - up) / 2.0
    return offset, float(thick), (up_term and down_term)


def _find_band(lab, axis, along, perp, ref, max_t, search=None):
    """Search perpendicular to the line for the nearest contiguous band of
    line-colored (within CONT_TOL of `ref`) pixels around `perp`, at the given
    `along` coordinate. Returns (band_midpoint_perp, thickness) or None.

    Used by the SAVE maneuver: when the slant has shifted the band away from our
    fixed perp coordinate, find where it went and re-center. The band must be
    <= max_t thick (a real gutter); a wider run means we've reached a background
    region, so return None to end the line."""
    h, w = lab.shape[:2]
    if search is None:
        # The save fires when the slant drifts us off the band. With a 1px along
        # step and <=25deg tilt the band moves <=~0.5px/step, but we only save
        # after color fails (drift ~ thickness). Search a window of one
        # thickness + a small margin — NOT wide enough to jump to the next
        # parallel gutter (that would corrupt the trace).
        search = max_t + 2
    # gather the perpendicular strip and a same-color mask
    if axis == 1:                       # perp is vertical -> column `along`
        ix = int(round(along))
        if not (0 <= ix < w):
            return None
        lo = max(0, int(round(perp)) - search); hi = min(h, int(round(perp)) + search + 1)
        strip = lab[lo:hi, ix, :]
    else:                               # perp is horizontal -> row `along`
        iy = int(round(along))
        if not (0 <= iy < h):
            return None
        lo = max(0, int(round(perp)) - search); hi = min(w, int(round(perp)) + search + 1)
        strip = lab[iy, lo:hi, :]
    diff = strip - ref
    de = np.sqrt(np.einsum('ij,ij->i', diff, diff))
    same = de < CONT_TOL
    if not same.any():
        return None
    # find contiguous runs; choose the one nearest the query perp
    q = int(round(perp)) - lo
    idx = np.where(same)[0]
    runs = []
    s = idx[0]; p = idx[0]
    for v in idx[1:]:
        if v == p + 1:
            p = v
        else:
            runs.append((s, p)); s = v; p = v
    runs.append((s, p))
    best = min(runs, key=lambda r: min(abs(r[0] - q), abs(r[1] - q),
                                       0 if r[0] <= q <= r[1] else 10**9))
    thickness = best[1] - best[0] + 1
    if thickness > max_t:
        return None
    mid = (best[0] + best[1]) / 2.0 + lo
    return mid, float(thickness)


def _perp_centerline(lab, axis, px, py, ref, max_t):
    """Centerline + thickness perpendicular to the line at (px,py).
    Horizontal line (axis=1): normal is vertical -> scan column px.
    Vertical line (axis=0): normal is horizontal -> scan row py.
    Reads a short strip from `lab` directly (no full-image mask)."""
    h, w = lab.shape[:2]
    ix, iy = int(round(px)), int(round(py))
    # Strip must be wide enough to reach the band boundary even when the gutter
    # is fused with same-colour tile content; max_t no longer bounds the walk.
    pad = PERP_SCAN
    if axis == 1:
        if not (0 <= ix < w) or not (0 <= iy < h):
            return None
        lo = max(0, iy - pad); hi = min(h, iy + pad + 1)
        strip = lab[lo:hi, ix, :]
        r = _perp_from_strip(strip, iy - lo, ref, max_t)
        if r is None:
            return None
        off, thick, term = r
        return px, iy + off, thick, term
    else:
        if not (0 <= iy < h) or not (0 <= ix < w):
            return None
        lo = max(0, ix - pad); hi = min(w, ix + pad + 1)
        strip = lab[iy, lo:hi, :]
        r = _perp_from_strip(strip, ix - lo, ref, max_t)
        if r is None:
            return None
        off, thick, term = r
        return ix + off, py, thick, term


def _seed_thickness(lab, axis, cx, cy, ref):
    """Count contiguous pixels perpendicular to the gutter at (cx,cy) that match
    `ref` (within COLOR_TOL), capped at PERP_SCAN each side. Used ONLY to populate
    PotentialGridLine.thickness for the relative GRID_THICK_TOL gate — it never
    rejects a trace, so a gutter fused with same-colour tile content (large count)
    is fine."""
    h, w = lab.shape[:2]
    ix, iy = int(round(cx)), int(round(cy))
    if not (0 <= iy < h and 0 <= ix < w):
        return 1.0
    up = dn = 0
    if axis == 1:                       # perpendicular = vertical (column)
        i = iy - 1
        while i >= 0 and up < PERP_SCAN and _de2(lab[i, ix], ref) <= COLOR_TOL ** 2:
            up += 1; i -= 1
        i = iy + 1
        while i < h and dn < PERP_SCAN and _de2(lab[i, ix], ref) <= COLOR_TOL ** 2:
            dn += 1; i += 1
    else:                               # perpendicular = horizontal (row)
        i = ix - 1
        while i >= 0 and up < PERP_SCAN and _de2(lab[iy, i], ref) <= COLOR_TOL ** 2:
            up += 1; i -= 1
        i = ix + 1
        while i < w and dn < PERP_SCAN and _de2(lab[iy, i], ref) <= COLOR_TOL ** 2:
            dn += 1; i += 1
    return float(up + dn + 1)


# ── Stage B: center-out tracer ──────────────────────────────────────────────
def _seed_order(mid, lo, hi, step=1):
    yield mid
    d = step
    while True:
        a, b = mid - d, mid + d
        emitted = False
        if a >= lo:
            yield a; emitted = True
        if b <= hi:
            yield b; emitted = True
        if not emitted:
            break
        d += step


def _split_runs(sorted_idx):
    """Split a sorted index array into maximal runs of consecutive integers.
    Vectorized: a run boundary is wherever the gap to the next index is > 1, so
    np.diff locates all breaks in one pass and np.split cuts there. Returns a list
    of int arrays (callers do len()/indexing only)."""
    sorted_idx = np.asarray(sorted_idx)
    if sorted_idx.size == 0:
        return []
    breaks = np.where(np.diff(sorted_idx) > 1)[0] + 1
    return np.split(sorted_idx, breaks)


# ── Vectorized batched walk ──────────────────────────────────────────────────
# All seeds of one axis are walked in LOCKSTEP: at step k, every still-active
# trace is advanced one pixel along the gutter together with a single fancy-index
# gather + a vectorized accept test (replacing ~1976 sequential python pixel
# walks/img with ~image-dim vectorized steps). The accept test and the
# perpendicular slant re-find reproduce _trace_one's scalar `ok`/`extend` exactly:
#   ok:  |L - seed_L| <= SEED_L_TOL  AND  |L - prev_L| <= STEP_L_TOL
#        AND ΔE(pix, running line mean)^2 <= CONT_TOL2
#   refind: on a miss, look ±1..PERP_REFIND perpendicular (nearest first, up then
#           down) for a pixel that passes ok; if found, follow the slant, else stop.
def _batch_gather(lab, axis, along_i, perp_i):
    """lab pixels at integer (along, perp) for this axis -> (M,3)."""
    if axis == 1:        # H line: along=x, perp=y
        return lab[perp_i, along_i]
    return lab[along_i, perp_i]   # V line: along=y, perp=x


def _batch_accept(pix, seed_L, prev_L, line_color):
    """Vectorized copy of _trace_one.ok over M traces at once."""
    L = pix[:, 0]
    d0 = pix[:, 0] - line_color[:, 0]
    d1 = pix[:, 1] - line_color[:, 1]
    d2 = pix[:, 2] - line_color[:, 2]
    de2 = d0 * d0 + d1 * d1 + d2 * d2
    return ((np.abs(L - seed_L) <= SEED_L_TOL)
            & (np.abs(L - prev_L) <= STEP_L_TOL)
            & (de2 <= CONT_TOL2))


def _walk_dir(lab, axis, along_seed, perp_seed, seed_ref, direction):
    """Walk all N seeds one direction in lockstep. Returns (last_along[N],
    last_perp[N], support[N]). Reproduces _trace_one.extend exactly: per-step
    accept test + ±PERP_REFIND perpendicular slant re-find (nearest first, up
    then down). State arrays are indexed by ORIGINAL seed id; an `active` mask
    compacts the work each step."""
    h, w = lab.shape[:2]
    N = along_seed.shape[0]
    along_max = (w - 1) if axis == 1 else (h - 1)
    perp_max = (h - 1) if axis == 1 else (w - 1)
    seed_L = seed_ref[:, 0]

    perp = perp_seed.astype(np.float64).copy()
    along = along_seed.astype(np.float64).copy()
    prev_L = seed_L.copy()
    line_color = seed_ref.astype(np.float64).copy()
    line_n = np.ones(N, dtype=np.float64)
    last_along = along_seed.astype(np.float64).copy()
    last_perp = perp_seed.astype(np.float64).copy()
    support = np.zeros(N, dtype=np.int64)
    active = np.ones(N, dtype=bool)

    while active.any():
        ai = np.where(active)[0]                     # original ids still walking
        al = along[ai] + direction * STEP
        in_b = (al >= 0) & (al <= along_max)
        if not in_b.all():
            active[ai[~in_b]] = False
            keep = in_b
            ai = ai[keep]; al = al[keep]
            if ai.size == 0:
                break
        pe = perp[ai]
        ali = al.astype(np.intp); pei = pe.astype(np.intp)
        pix = _batch_gather(lab, axis, ali, pei)
        ok = _batch_accept(pix, seed_L[ai], prev_L[ai], line_color[ai])

        new_perp = pe.copy()
        new_pix = pix.copy()
        accepted = ok.copy()

        if not ok.all():
            # `still` is a boolean over positions-within-ai: traces still looking
            # for a slant continuation. Nearest offset first (d=1..), up then down;
            # once a trace finds the gutter colour it drops out of `still`.
            still = ~ok
            for d in range(1, PERP_REFIND + 1):
                for s in (-1, +1):
                    sid = np.where(still)[0]
                    if sid.size == 0:
                        break
                    cand_pe = pe[sid] + s * d
                    inb = (cand_pe >= 0) & (cand_pe <= perp_max)
                    sid = sid[inb]; cand_pe = cand_pe[inb]
                    if sid.size == 0:
                        continue
                    cpx = _batch_gather(lab, axis, al[sid].astype(np.intp),
                                        cand_pe.astype(np.intp))
                    passc = _batch_accept(cpx, seed_L[ai[sid]],
                                          prev_L[ai[sid]], line_color[ai[sid]])
                    hit = sid[passc]
                    if hit.size:
                        new_perp[hit] = cand_pe[passc]
                        new_pix[hit] = cpx[passc]
                        accepted[hit] = True
                        still[hit] = False
                if not still.any():
                    break

        adv = np.where(accepted)[0]
        if adv.size:
            gid = ai[adv]
            ln = line_n[gid]
            line_color[gid] = (line_color[gid] * ln[:, None] + new_pix[adv]) / (ln[:, None] + 1)
            line_n[gid] = ln + 1
            prev_L[gid] = new_pix[adv, 0]
            perp[gid] = new_perp[adv]
            along[gid] = al[adv]
            last_perp[gid] = new_perp[adv]
            last_along[gid] = al[adv]
            support[gid] += 1
        active[ai[~accepted]] = False                # no continuation -> stop

    return last_along, last_perp, support


def _walk_batch(lab, axis, along_seed, perp_seed, seed_ref):
    """Walk N seeds both directions in lockstep. Returns a_lo,a_hi (min/max along
    reached), perp_lo,perp_hi (perp at those ends), support (steps both dirs)."""
    fa, fp, ns_f = _walk_dir(lab, axis, along_seed, perp_seed, seed_ref, +1)
    ba, bp, ns_b = _walk_dir(lab, axis, along_seed, perp_seed, seed_ref, -1)
    a_hi = np.maximum(along_seed.astype(np.float64), fa)
    a_lo = np.minimum(along_seed.astype(np.float64), ba)
    return a_lo, a_hi, bp, fp, ns_f + ns_b


def _trace_one(lab, axis, run, seed_along, seed_bias):
    """Trace a line. `run` is the contiguous ridge-run of perpendicular-axis
    indices (x's for a horizontal line, y's for a vertical line); `seed_along`
    is the fixed coordinate on the seed row/col. We pick a seed position within
    the run whose perpendicular probe succeeds (the run midpoint can land on a
    1-px dead spot of a real gutter), so a single unlucky pixel doesn't drop the
    whole line."""
    h, w = lab.shape[:2]
    # Seed = the run midpoint (or quarters) whose pixel is a clean gutter colour.
    # No thickness/termination check here — a gutter fused with same-colour tile
    # content has no measurable thickness, but it is still a real gutter.
    mid = len(run) // 2
    order = [mid]
    for frac in (0.25, 0.75):
        idx = int(len(run) * frac)
        if 0 <= idx < len(run) and idx not in order:
            order.append(idx)
    cx = cy = None
    for idx in order:
        along = float(run[idx])
        if axis == 1:
            sx, sy = along, float(seed_along)
        else:
            sx, sy = float(seed_along), along
        iy, ix = int(round(sy)), int(round(sx))
        if 0 <= iy < h and 0 <= ix < w:
            cx, cy = sx, sy
            break
    if cx is None:
        return None
    if axis == 1:               # horizontal line: along=x, perp=y
        along_seed, perp_seed = cx, cy
    else:                       # vertical line: along=y, perp=x
        along_seed, perp_seed = cy, cx
    seed_ref = lab[int(round(cy)), int(round(cx))].astype(np.float64)

    def at(x, y):
        # nearest-pixel LAB lookup; int() truncation is fine at our scale and
        # avoids the per-call cost of round()+astype() in the inner walk loop.
        return lab[int(y + 0.5), int(x + 0.5)]

    def perp_at(along, perp):
        """The (x,y) pixel at along/perp for this axis."""
        return (along, perp) if axis == 1 else (perp, along)

    def drift():
        """Walk along the gutter one pixel at a time. At each step compare the
        pixel to the running gutter colour. If it still matches -> advance. If it
        DOESN'T (we hit a cell), it may just be the SLANT carrying the gutter off
        our current perp row/col: look a few px perpendicular (up then down) for
        the gutter colour and, if found, STEP THERE and keep going. Only if no
        gutter colour exists within +/-PERP_REFIND perpendicular is it a genuine
        wall -> stop. No thickness, no midpoint: perp just tracks the gutter."""
        line_color = seed_ref.copy()
        line_n = 1
        endpoints = []
        seed_L = float(seed_ref[0])

        def ok(pix, prev_L):
            """Accept `pix` as part of the gutter. Lightness (L) is the stable
            signal — a real gutter's L barely moves (measured span ~3) while a
            shaded surface like grass drifts wildly (span ~32). a/b are noisy
            (JPEG chroma) and visually irrelevant at these lightnesses, so we do
            NOT gate on full ΔE-from-seed (that over-penalised chroma noise and
            dropped real tinted-grey gutters). Three checks:
              1. |L - seed_L| <= SEED_L_TOL          (same lightness throughout)
              2. |L - prev_L| <= STEP_L_TOL          (no lightness jump step->step)
              3. ΔE(pix, running mean) <= CONT_TOL    (local colour continuity)"""
            L = float(pix[0])
            return (abs(L - seed_L) <= SEED_L_TOL
                    and abs(L - prev_L) <= STEP_L_TOL
                    and _de2(pix, line_color) <= CONT_TOL2)

        def extend(direction):
            nonlocal line_color, line_n
            along, perp = along_seed, perp_seed
            prev_L = seed_L
            n = 0
            while True:
                along += direction * STEP
                px, py = perp_at(along, perp)
                if px < 0 or py < 0 or px > w - 1 or py > h - 1:
                    break
                cpix = at(px, py)
                if ok(cpix, prev_L):
                    line_color = (line_color * line_n + cpix) / (line_n + 1); line_n += 1
                    prev_L = float(cpix[0])
                    n += 1
                    continue
                # Colour changed: hit a cell. Is the gutter just slanted away?
                # Look perpendicular for the gutter colour (same test), nearest
                # offset first.
                found = None
                for d in range(1, PERP_REFIND + 1):
                    for s in (-1, +1):                  # up then down
                        np_ = perp + s * d
                        qx, qy = perp_at(along, np_)
                        if qx < 0 or qy < 0 or qx > w - 1 or qy > h - 1:
                            continue
                        qpix = at(qx, qy)
                        if ok(qpix, prev_L):
                            found = np_; break
                    if found is not None:
                        break
                if found is None:
                    break                               # genuine wall -> stop
                perp = found                            # follow the slant
                qx, qy = perp_at(along, perp)
                cpix = at(qx, qy)
                line_color = (line_color * line_n + cpix) / (line_n + 1); line_n += 1
                prev_L = float(cpix[0])
                n += 1
            endpoints.append(perp_at(along, perp))
            return n

        n_fwd = extend(+1)
        n_bwd = extend(-1)
        return endpoints, n_fwd + n_bwd

    def fit(endpoints):
        """Fit (ang, slant, midline, span) through seed + traced endpoints."""
        pts = np.array([(cx, cy)] + endpoints, dtype=np.float64)
        mean = pts.mean(axis=0)
        dv = _principal_dir_2d(pts - mean)
        if axis == 1:
            ang = np.arctan2(dv[1], dv[0])
            if abs(ang) > np.pi / 2:
                ang -= np.copysign(np.pi, ang)
            slant = np.tan(ang)
            midline = mean[1] + slant * (w / 2.0 - mean[0])
            span = float(pts[:, 0].max() - pts[:, 0].min())
        else:
            ang = np.arctan2(dv[0], dv[1])
            if abs(ang) > np.pi / 2:
                ang -= np.copysign(np.pi, ang)
            slant = np.tan(ang)
            midline = mean[0] + slant * (h / 2.0 - mean[1])
            span = float(pts[:, 1].max() - pts[:, 1].min())
        return pts, ang, slant, midline, span

    # Walk the gutter both directions (slant followed by perpendicular re-find,
    # not by thickness/midpoint). The slant is recovered from the fit through the
    # traced endpoints.
    endpoints, support = drift()
    if support < MIN_RUN:
        return None
    pts, ang, slant, midline, span = fit(endpoints)
    full = w if axis == 1 else h
    if abs(slant) > SLANT_CAP or span < SUPPORT_FRAC * full:
        return None
    a_lo = float(pts[:, 0].min()) if axis == 1 else float(pts[:, 1].min())
    a_hi = float(pts[:, 0].max()) if axis == 1 else float(pts[:, 1].max())

    # Thickness for the consistency gate is measured once at the seed (it does NOT
    # gate the trace). Count contiguous gutter-colour pixels perpendicular to the
    # seed; if the gutter is fused with same-colour tile content the count is
    # large but bounded by PERP_SCAN — only used for the relative thickness gate.
    th_med = _seed_thickness(lab, axis, cx, cy, seed_ref)
    # Sample colours along the validated line for the consistency gate + mean.
    cols = []
    for a in np.arange(a_lo, a_hi + 1, 3.0):
        if axis == 1:
            x = a; y = midline + slant * (x - w / 2.0)
        else:
            y = a; x = midline + slant * (y - h / 2.0)
        jx, jy = int(round(x)), int(round(y))
        if 0 <= jy < h and 0 <= jx < w:
            cols.append(lab[jy, jx])
    if not cols:
        return None
    cols = np.array(cols, dtype=np.float64)
    color_std = float(np.mean(np.std(cols, axis=0)))
    if color_std > LINE_STD_TOL:
        return None

    # Endpoints from the validated (a_lo,a_hi) span on the fitted line.
    if axis == 1:
        start = (a_lo, midline + slant * (a_lo - w / 2.0))
        end = (a_hi, midline + slant * (a_hi - w / 2.0))
    else:
        start = (midline + slant * (a_lo - h / 2.0), a_lo)
        end = (midline + slant * (a_hi - h / 2.0), a_hi)

    return PotentialGridLine(
        orientation='h' if axis == 1 else 'v',
        angle=float(ang), thickness=th_med,
        start=start, end=end,
        color_lab=cols.mean(axis=0), color_std=color_std,
        midline_pos=float(midline), support=int(support),
    )


def _principal_dir_2d(centered):
    """Unit eigenvector of the largest eigenvalue of the 2x2 covariance of
    `centered` (N,2) points. Closed form — no SVD."""
    cxx = float(np.dot(centered[:, 0], centered[:, 0]))
    cyy = float(np.dot(centered[:, 1], centered[:, 1]))
    cxy = float(np.dot(centered[:, 0], centered[:, 1]))
    # eigenvector of [[cxx,cxy],[cxy,cyy]] for the larger eigenvalue
    tr = cxx + cyy
    det = cxx * cyy - cxy * cxy
    disc = max(0.0, (tr * tr) / 4.0 - det)
    lam = tr / 2.0 + np.sqrt(disc)
    if abs(cxy) > 1e-9:
        v = np.array([lam - cyy, cxy], dtype=np.float64)
    elif cxx >= cyy:
        v = np.array([1.0, 0.0])
    else:
        v = np.array([0.0, 1.0])
    n = np.hypot(*v)
    return v / n if n > 1e-9 else np.array([1.0, 0.0])


def _scan_seeds(lab, axis, seed_bias, ridge, c_lo, c_hi, span, lines):
    """Seed EVERY row/col (no skip, no order-based dedup) and collect ALL valid
    traces. The same gutter is intentionally re-seeded from each of its rows; the
    duplicates are clustered and averaged later by _merge_lines. This replaces the
    old 'skip a band-width / first-found-wins' scheme — which let a weak slanted
    fragment found first block a stronger straight gutter found a few rows later
    (the missed-straight-gutter bug). Checking every row also recentres the line
    on the cluster midpoint, like find_grid's original cluster-and-average."""
    h, w = lab.shape[:2]
    lo, hi = int(span * (0.5 - MAX_SEED_FRAC)), int(span * (0.5 + MAX_SEED_FRAC))
    # Decimate seed rows/cols: a gutter is >= MIN_RUN px in the perpendicular
    # direction, so stepping by SEED_DECIMATE still seeds EVERY gutter from
    # multiple rows (enough for the cluster-average) while doing a fraction of the
    # per-seed trace work. The dominant cost is tracing the thousands of seeds that
    # land on tile CONTENT and die after a few px; decimation cuts that linearly.
    for c in range(lo, hi + 1, SEED_DECIMATE):
        if axis == 1:
            runs = _split_runs(np.where(ridge[c, c_lo:c_hi])[0] + c_lo)
        else:
            runs = _split_runs(np.where(ridge[c_lo:c_hi, c])[0] + c_lo)
        for run in runs:
            if len(run) < MIN_RUN:
                continue
            ln = _trace_one(lab, axis, run, float(c), seed_bias)
            if ln is not None:
                lines.append(ln)


def _collect_seeds(lab, axis, ridge, c_lo, c_hi, span):
    """Gather one seed per (decimated row/col, ridge-run >= MIN_RUN), choosing the
    seed pixel exactly as _trace_one does (run midpoint, then 1/4 and 3/4 as
    fallbacks) restricted to in-bounds. Returns arrays along_seed[N], perp_seed[N],
    seed_ref[N,3] for the batch walk."""
    h, w = lab.shape[:2]
    lo, hi = int(span * (0.5 - MAX_SEED_FRAC)), int(span * (0.5 + MAX_SEED_FRAC))
    scan_w = c_hi - c_lo
    along_s, perp_s = [], []
    for c in range(lo, hi + 1, SEED_DECIMATE):
        if axis == 1:                       # H line: c is a row (y=perp), run is x (along)
            runs = _split_runs(np.where(ridge[c, c_lo:c_hi])[0] + c_lo)
        else:                               # V line: c is a col (x=perp), run is y (along)
            runs = _split_runs(np.where(ridge[c_lo:c_hi, c])[0] + c_lo)
        for run in runs:
            if len(run) < MIN_RUN or len(run) < SEED_RUN_FRAC * scan_w:
                # A real grid gutter is seeded from a row/col that lies ON it, so
                # its ridge run already spans almost the whole scan (measured:
                # survivors >= 0.88 of scan; dying content fragments median ~0.09,
                # p90 ~0.35). Requiring the run to cover >= SEED_RUN_FRAC of the
                # scan drops ~95% of the dead content seeds BEFORE the expensive
                # walk (they accounted for ~73% of all walk steps) without losing a
                # real gutter. Slant is recovered DURING the walk (perp re-find),
                # not from the seed run, so this does not hurt tilted grids.
                continue
            # seed-pixel choice (mirror _trace_one): midpoint, then quarters
            chosen = None
            mid = len(run) // 2
            for idx in [mid, int(len(run) * 0.25), int(len(run) * 0.75)]:
                if 0 <= idx < len(run):
                    a = float(run[idx])
                    if axis == 1:
                        sx, sy = a, float(c)
                    else:
                        sx, sy = float(c), a
                    if 0 <= int(sy + 0.5) < h and 0 <= int(sx + 0.5) < w:
                        chosen = a; break
            if chosen is None:
                continue
            along_s.append(chosen)
            perp_s.append(float(c))
    if not along_s:
        return (np.empty(0), np.empty(0), np.empty((0, 3)))
    along_seed = np.array(along_s, dtype=np.float64)
    perp_seed = np.array(perp_s, dtype=np.float64)
    if axis == 1:                           # gather at (perp=y, along=x)
        seed_ref = lab[perp_seed.astype(np.intp), along_seed.astype(np.intp)].astype(np.float64)
    else:                                   # gather at (along=y, perp=x)
        seed_ref = lab[along_seed.astype(np.intp), perp_seed.astype(np.intp)].astype(np.float64)
    return along_seed, perp_seed, seed_ref


def _trace_lines(lab, axis, seed_bias):
    """Vectorized: collect all seeds, walk them in lockstep (_walk_batch), then
    build a PotentialGridLine per surviving trace with the same per-line gates as
    the scalar path (SLANT_CAP, SUPPORT_FRAC span, LINE_STD_TOL)."""
    h, w = lab.shape[:2]
    ridge = _build_ridge_map(lab, axis)
    if axis == 1:
        span = h; c_lo, c_hi = int(w * 0.2), int(w * 0.8)
    else:
        span = w; c_lo, c_hi = int(h * 0.2), int(h * 0.8)
    along_seed, perp_seed, seed_ref = _collect_seeds(lab, axis, ridge, c_lo, c_hi, span)
    if along_seed.size == 0:
        return []
    a_lo, a_hi, perp_lo, perp_hi, support = _walk_batch(lab, axis, along_seed, perp_seed, seed_ref)

    full = w if axis == 1 else h
    lines = []
    for i in range(along_seed.size):
        if support[i] < MIN_RUN:
            continue
        span_i = a_hi[i] - a_lo[i]
        if span_i < SUPPORT_FRAC * full:
            continue
        # endpoints traced: (a_lo, perp_lo) and (a_hi, perp_hi) in along/perp;
        # plus the seed. Fit a line through them (PCA), exactly like _trace_one.fit.
        if axis == 1:
            pts = np.array([(along_seed[i], perp_seed[i]),
                            (a_lo[i], perp_lo[i]), (a_hi[i], perp_hi[i])], dtype=np.float64)
        else:
            pts = np.array([(perp_seed[i], along_seed[i]),
                            (perp_lo[i], a_lo[i]), (perp_hi[i], a_hi[i])], dtype=np.float64)
        mean = pts.mean(axis=0)
        dv = _principal_dir_2d(pts - mean)
        if axis == 1:
            ang = np.arctan2(dv[1], dv[0])
            if abs(ang) > np.pi / 2:
                ang -= np.copysign(np.pi, ang)
            slant = np.tan(ang)
            midline = mean[1] + slant * (w / 2.0 - mean[0])
        else:
            ang = np.arctan2(dv[0], dv[1])
            if abs(ang) > np.pi / 2:
                ang -= np.copysign(np.pi, ang)
            slant = np.tan(ang)
            midline = mean[0] + slant * (h / 2.0 - mean[1])
        if abs(slant) > SLANT_CAP:
            continue
        a0 = a_lo[i]; a1 = a_hi[i]
        # sample colours along the validated line for the consistency gate + mean
        aa = np.arange(a0, a1 + 1, 3.0)
        if axis == 1:
            xs = aa; ys = midline + slant * (xs - w / 2.0)
        else:
            ys = aa; xs = midline + slant * (ys - h / 2.0)
        jx = np.round(xs).astype(np.intp); jy = np.round(ys).astype(np.intp)
        inb = (jy >= 0) & (jy < h) & (jx >= 0) & (jx < w)
        if not inb.any():
            continue
        cols = lab[jy[inb], jx[inb]].astype(np.float64)
        color_std = float(np.mean(np.std(cols, axis=0)))
        if color_std > LINE_STD_TOL:
            continue
        if axis == 1:
            start = (a0, midline + slant * (a0 - w / 2.0))
            end = (a1, midline + slant * (a1 - w / 2.0))
        else:
            start = (midline + slant * (a0 - h / 2.0), a0)
            end = (midline + slant * (a1 - h / 2.0), a1)
        lines.append(PotentialGridLine(
            orientation='h' if axis == 1 else 'v',
            angle=float(ang), thickness=0.0,
            start=start, end=end,
            color_lab=cols.mean(axis=0), color_std=color_std,
            midline_pos=float(midline), support=int(support[i]),
        ))
    return _merge_lines(lines)


def _merge_lines(lines):
    """Cluster duplicate traces of the same gutter (seeded from each of its rows)
    by POSITION ONLY and collapse each cluster to one line. We deliberately do
    NOT also gate on angle: noisy slanted fragments of the SAME gutter get
    slightly different angle estimates, and gating on angle left them as separate
    near-duplicate lines that polluted the line list and broke extraction. Two
    genuinely different gutters are >= MIN_CELL apart, far beyond MERGE_PX, so
    position-only clustering can't fuse distinct gutters."""
    if not lines:
        return []
    lines = sorted(lines, key=lambda l: l.midline_pos)
    merged = []
    cur = [lines[0]]
    for ln in lines[1:]:
        if abs(ln.midline_pos - cur[-1].midline_pos) < MERGE_PX:
            cur.append(ln)
        else:
            merged.append(_pick(cur)); cur = [ln]
    merged.append(_pick(cur))
    return merged


def _pick(group):
    """Collapse a cluster of duplicate traces of the same gutter into one line.
    Keep the strongest member's attributes (angle, colour, thickness) but set the
    position to the SUPPORT-WEIGHTED mean — so a real straight full-width gutter
    (large support, many duplicate rows) dominates the centre and a weak slanted
    fragment that happens to fall in the same cluster barely shifts it. `support`
    becomes the cluster's max (its real length), and we record the cluster size."""
    best = max(group, key=lambda l: l.support)
    wsum = sum(l.support for l in group)
    best.midline_pos = sum(l.midline_pos * l.support for l in group) / wsum
    best.support = max(l.support for l in group)
    return best


# ── _generate_grid: cell boxes for a (rows x cols) grid of any size ──────────
def _generate_grid(rows, cols, hs, vs, hd, vd, h, w, slant):
    """Cell bounding boxes for a `rows` x `cols` grid. `hs` are the `rows-1`
    internal H-line positions (pitch hd), `vs` the `cols-1` internal V-lines
    (pitch vd). The outer borders are extrapolated one pitch beyond the outer
    internal lines (grids have no reliable outer frame; the central pitch is the
    signal). Returns rows*cols boxes row-major, or None if any clip empty.
    Generalises the old 3x3/4x4-only generator to any dimensions."""
    mid_x, mid_y = w / 2, h / 2
    y_bounds = [hs[0] - hd] + list(hs) + [hs[-1] + hd]
    x_bounds = [vs[0] - vd] + list(vs) + [vs[-1] + vd]
    grid_boxes = []
    s2 = slant * slant
    denom = 1 + s2
    for i in range(len(y_bounds) - 1):
        for j in range(len(x_bounds) - 1):
            corners = []
            for y_i in [y_bounds[i], y_bounds[i + 1]]:
                for x_j in [x_bounds[j], x_bounds[j + 1]]:
                    cy = (y_i + slant * (x_j - mid_x) + s2 * mid_y) / denom
                    cx = x_j - slant * (cy - mid_y)
                    corners.append((cx, cy))
            pts = np.array(corners, dtype=np.float32)
            x1, y1 = np.min(pts, axis=0)
            x2, y2 = np.max(pts, axis=0)
            x1_c, y1_c = int(max(0, min(w, x1))), int(max(0, min(h, y1)))
            x2_c, y2_c = int(max(0, min(w, x2))), int(max(0, min(h, y2)))
            if x2_c > x1_c and y2_c > y1_c:
                grid_boxes.append((x1_c, y1_c, x2_c, y2_c))
    return grid_boxes if len(grid_boxes) == rows * cols else None


# ── Stage C: extract grid from traced lines ─────────────────────────────────
def _internal(lines, total):
    return [l for l in lines
            if total * EDGE_MARGIN < l.midline_pos < total * (1 - EDGE_MARGIN)]


def _even_spacing_ok(positions, total):
    """True if `positions` (sorted internal-line coords) are EVENLY spaced — every
    consecutive INTERNAL gap is within EVEN_TOL of the median. This is the core
    "cells are the same size" rule, measured from the central closed cells (the
    reliable part). We do NOT require or validate an outer border: real captchas
    often have no clean frame and the grid bleeds to the image edge. Instead we
    take the pitch from the internal gaps and only sanity-check that extrapolating
    one pitch beyond each outer internal line keeps the implied grid inside the
    image (it can't extend far past the edge). Returns (ok, pitch)."""
    p = sorted(positions)
    gaps = [p[i + 1] - p[i] for i in range(len(p) - 1)]
    pitch = float(np.median(gaps))
    if pitch < MIN_CELL:
        return False, pitch
    # equal-size cells: every internal gap matches the median pitch
    for g in gaps:
        if abs(g - pitch) > EVEN_TOL * pitch:
            return False, pitch
    # The implied grid spans [p[0]-pitch, p[-1]+pitch] (one cell beyond each outer
    # internal line). Require it to fit within the image with only a small
    # overshoot — the grid can reach/slightly exceed the edge (no border needed),
    # but a pair of lines whose extrapolated grid falls far outside the image is
    # not a real grid. Allows up to GRID_OVERSHOOT*pitch past each edge.
    if (p[0] - pitch) < -GRID_OVERSHOOT * pitch:
        return False, pitch
    if (p[-1] + pitch) > total + GRID_OVERSHOOT * pitch:
        return False, pitch
    return True, pitch


def _line_span_perp(line):
    """(lo, hi) extent of a line along its OWN run direction (H -> x span, V -> y
    span) — i.e. how far it reaches in the perpendicular axis' coordinate."""
    if line.orientation == 'h':
        return (min(line.start[0], line.end[0]), max(line.start[0], line.end[0]))
    return (min(line.start[1], line.end[1]), max(line.start[1], line.end[1]))


def _corroborate(lines, perp_lines, total):
    """Keep only lines that are REAL grid separators, corroborated by the
    perpendicular lines: a true internal line has >=2 perpendicular lines crossing
    it that extend at least ~CORROB_FRAC of a cell PAST it on BOTH sides. A frame /
    chrome line at the grid edge fails — the perpendicular lines die at the edge and
    do not continue a full cell beyond it (the giveaway the user pointed out: the V
    gutters above the top H line only continue a pixel or two). The grid is then
    always treated as OPEN (no border reliance): the surviving lines are internal
    separators and the outer cells are extrapolated one pitch out.

    `lines` are candidates on one axis; `perp_lines` the traced lines of the other
    axis; `total` the image dim along `lines`' position axis. Cell size ~ the
    perpendicular lines' spacing (square cells)."""
    lines = sorted(_internal(lines, total), key=lambda l: l.midline_pos)
    perp = sorted(perp_lines, key=lambda l: l.midline_pos)
    if len(lines) < 2:
        return lines
    # cell size estimate: median gap between consecutive candidate lines (this axis)
    pos = [l.midline_pos for l in lines]
    gaps = np.diff(pos)
    cell = float(np.median(gaps)) if len(gaps) else 0.0
    if cell < MIN_CELL:
        return lines
    need = CORROB_FRAC * cell
    kept = []
    for l in lines:
        p = l.midline_pos                     # this line's position (y for H, x for V)
        n_ok = 0
        for q in perp:
            lo, hi = _line_span_perp(q)        # the perp line's extent in THIS axis' coord
            # does q cross l and extend >= need (~1 FULL cell) past it on BOTH
            # sides? A true internal separator has the perpendicular gutters
            # running a full cell beyond it on each side (there is a real cell
            # there). A frame/edge line fails: the perpendicular gutters only reach
            # the grid border — LESS than a full cell past — because there is no
            # cell beyond the edge.
            if (p - lo) >= need and (hi - p) >= need:
                n_ok += 1
        if n_ok >= 2:
            kept.append(l)
    # need at least 2 corroborated lines to define an open grid (>=3 cells)
    return kept if len(kept) >= 2 else lines


def _complete_one_run(positions, grp, pitch, total):
    """Given a REAL evenly-spaced run of internal lines (positions == grp midlines,
    common pitch), yield completed runs that add at most MAX_VIRTUAL_NODES virtual
    internal lines by:
      * INTERPOLATING any ~k*pitch interior gap (a missing internal gutter between
        two same-colour cells — the sky-bordered-row case), and
      * EXTRAPOLATING one extra node beyond each end (a missing OUTER internal line
        that would complete a larger grid).
    `positions` carry the real midlines; virtual nodes sit exactly on the run's
    pitch. Yields (completed_positions, n_virtual). The caller scores + gates them;
    the cell-content gate is what ultimately rejects an over-extrapolation onto
    background. Anchoring on a real consecutive run (not arbitrary clean pairs)
    means a spurious off-pitch clean line can never seed a wrong lattice."""
    real = sorted(positions)
    # Candidate TRUE pitches: the run's own pitch, plus its sub-multiples — a
    # 2-line run's "pitch" is the whole gap, which may actually straddle k missing
    # cells (e.g. 222..413 is one missing internal line at 317, true pitch ~95).
    sub_pitches = [pitch]
    for k in (2, 3):
        sp = pitch / k
        if sp >= MIN_CELL:
            sub_pitches.append(sp)
    bases = []
    seen_runs = set()
    for tp in sub_pitches:
        # 1) interior fill: insert virtual nodes wherever a gap is ~m*tp (m>=2).
        filled = [real[0]]
        interior_virtual = 0
        ok = True
        for a, b in zip(real, real[1:]):
            m = int(round((b - a) / tp))
            if m < 1 or abs((b - a) - m * tp) > EVEN_TOL * tp:
                ok = False
                break
            for j in range(1, m):
                filled.append(a + j * (b - a) / m)
                interior_virtual += 1
            filled.append(b)
        if not ok:
            continue
        kf = tuple(round(x) for x in filled)
        if kf in seen_runs:
            continue
        seen_runs.add(kf)
        bases.append((list(filled), interior_virtual))
    # 2) extrapolation: 0 or 1 node beyond each end, on the base's own pitch.
    out = []
    for base, ivirt in bases:
        bp = (base[-1] - base[0]) / (len(base) - 1) if len(base) > 1 else pitch
        for lo_add in (0, 1):
            for hi_add in (0, 1):
                nv = ivirt + lo_add + hi_add
                if nv == 0 or nv > MAX_VIRTUAL_NODES:
                    continue
                run = list(base)
                if lo_add:
                    run.insert(0, run[0] - bp)
                if hi_add:
                    run.append(run[-1] + bp)
                dim = len(run) + 1
                if dim < MIN_GRID_DIM or dim > 6:
                    continue
                if nv > MAX_VIRTUAL_FRAC * len(run):
                    continue
                out.append((run, nv))
    return out


def _completed_candidates(lines, total, real_cand):
    """Lattice completion from clean PARTIAL runs.

    Some real grids are missing one or more internal gutters because that gutter
    borders two same-colour cells (e.g. a reCAPTCHA 4x4 whose top rows are sky:
    the internal line between two sky tiles has no colour change to trace). The
    present gutters still establish the pitch; the missing line's position is fully
    determined by it. For every REAL candidate run (built by _axis_candidates from
    actual lines), emit completed runs whose `positions` add EXTRAPOLATED /
    INTERPOLATED virtual nodes, while `grp` keeps only the REAL clean lines (so the
    colour / span / angle gates downstream judge real evidence only).

    Guard rails (this never invents grids on texture / FP images):
      * we only extend runs whose real anchor lines are CLEAN (color_std <
        CLEAN_LATTICE_STD) — painted gutters, not noisy photo edges;
      * a spurious off-pitch line cannot seed a lattice: we extend the REAL even
        runs, never arbitrary clean-line pairs;
      * at most MAX_VIRTUAL_NODES virtual nodes / MAX_VIRTUAL_FRAC of the lattice;
      * the completed run stays evenly spaced (re-checked via _even_spacing_ok).
    The decisive content gate (_cells_have_content) still runs in the extraction
    loop, so a completed lattice over a flat region (cells == gutter colour) is
    rejected there exactly like any other candidate."""
    out = {}
    seen = set()
    for dim, runs in real_cand.items():
        for positions, _score, pitch, grp in runs:
            # only extend clean painted runs
            if any(l.color_std >= CLEAN_LATTICE_STD for l in grp):
                continue
            for run, n_virtual in _complete_one_run(positions, grp, pitch, total):
                run = sorted(run)
                cdim = len(run) + 1
                ok, fpitch = _even_spacing_ok(run, total)
                if not ok:
                    continue
                key = (cdim, tuple(round(p) for p in run))
                if key in seen:
                    continue
                seen.add(key)
                ang_pen = max(l.angle for l in grp) - min(l.angle for l in grp)
                center_off = abs((run[0] + run[-1]) / 2 - total / 2) / total
                scale_err = abs(fpitch - total / cdim) / total
                # Penalise each invented node so a fully-real lattice of the SAME
                # dim always outranks a completed one, and fewer virtuals win.
                score = (center_off * 1000 + scale_err * 500 + ang_pen * 200
                         + n_virtual * VIRTUAL_NODE_PENALTY)
                out.setdefault(cdim, []).append((run, score, fpitch, grp))
    return out


def _axis_candidates(lines, total):
    """Enumerate evenly-spaced INTERNAL-separator runs of any length K>=2 -> a grid
    of K+1 cells along this axis (OPEN model: lines are internal, outer cells
    extrapolated one pitch beyond the ends; no border reliance). `lines` here are
    already corroboration-filtered, so frame/chrome lines are gone. Greedy run-grow
    from each (start, pitch) seed, snapping to the nearest line within EVEN_TOL.
    Returns {dim: [(internal_positions, score, pitch, internal_lines), ...]} keyed
    by cell-dimension dim = K+1."""
    lines = sorted(_internal(lines, total), key=lambda l: l.midline_pos)
    n = len(lines)
    cand = {}
    seen = set()
    for i in range(n):
        for j in range(i + 1, n):
            pitch0 = lines[j].midline_pos - lines[i].midline_pos
            if pitch0 < MIN_CELL:
                continue
            run_idx = [i, j]
            pos = lines[j].midline_pos
            jj = j
            while True:
                target = pos + pitch0
                best_k = None; best_d = EVEN_TOL * pitch0
                for k in range(jj + 1, n):
                    d = abs(lines[k].midline_pos - target)
                    if d < best_d:
                        best_d = d; best_k = k
                    if lines[k].midline_pos - target > EVEN_TOL * pitch0:
                        break
                if best_k is None:
                    break
                run_idx.append(best_k)
                pos = lines[best_k].midline_pos
                jj = best_k
            if len(run_idx) < 2:
                continue
            positions = [lines[r].midline_pos for r in run_idx]
            ok, pitch = _even_spacing_ok(positions, total)
            if not ok:
                continue
            grp = [lines[r] for r in run_idx]
            dim = len(run_idx) + 1            # OPEN: K internal lines -> K+1 cells
            if dim < MIN_GRID_DIM:
                continue
            key = (dim, tuple(round(p) for p in positions))
            if key in seen:
                continue
            seen.add(key)
            ang_pen = max(l.angle for l in grp) - min(l.angle for l in grp)
            center_off = abs((positions[0] + positions[-1]) / 2 - total / 2) / total
            scale_err = abs(pitch - total / dim) / total
            score = center_off * 1000 + scale_err * 500 + ang_pen * 200
            cand.setdefault(dim, []).append((positions, score, pitch, grp))
    # Lattice completion: add candidates that interpolate/extrapolate a missing
    # internal gutter from a clean partial run (sky-bordered grids). They carry a
    # virtual-node penalty so they only win when no fully-real lattice of the same
    # dimension exists, and are deduped against the real candidates above.
    for dim, comps in _completed_candidates(lines, total, dict(cand)).items():
        bucket = cand.setdefault(dim, [])
        existing = {tuple(round(p) for p in c[0]) for c in bucket}
        for c in comps:
            kpos = tuple(round(p) for p in c[0])
            if kpos not in existing:
                bucket.append(c)
                existing.add(kpos)
    for k in cand:
        cand[k].sort(key=lambda x: x[1])
    return cand


def extract_grid_from_lines(h_lines, v_lines, h, w, lab=None):
    """Identify a 3x3 or 4x4 grid from traced lines and emit row-major boxes.
    Returns (boxes, size, slant) or (None, None, None). All colour gates are
    RELATIVE — colour spread among the chosen separators, H-mean vs V-mean,
    same-as-gutter for the off-lattice count — never a check against a specific
    colour, so a grid of ANY uniform border colour is detectable. (`lab` is
    accepted for API symmetry; the active gates are line-based.)"""
    # Corroboration filter: drop frame/chrome lines (only KEEP lines crossed by
    # >=2 perpendicular lines that extend ~1 cell past them on both sides). Mutual:
    # filter H using V and V using H, on the raw traced lines.
    h_keep = _corroborate(h_lines, v_lines, h)
    v_keep = _corroborate(v_lines, h_lines, w)
    n_hkeep = len(_internal(h_keep, h))     # # corroborated internal lines per axis
    n_vkeep = len(_internal(v_keep, w))     # — a candidate should USE all of them
    h_cand = _axis_candidates(h_keep, h)
    v_cand = _axis_candidates(v_keep, w)
    best = None
    best_score = float('inf')
    # rows = (#internal H lines)+1, cols = (#internal V lines)+1; allow them to
    # DIFFER (rectangular grids like 6x4) — cells stay square (hd~vd), the grid
    # need not be. Enumerate every (rows, cols) with both dims >= MIN_GRID_DIM.
    for rows in sorted(h_cand):                     # keyed by cell-dimension now
        if rows < MIN_GRID_DIM:
            continue
        for cols in sorted(v_cand):
            if cols < MIN_GRID_DIM:
                continue
            for hpos, hsc, hd, hlns in h_cand[rows][:25]:
                for vpos, vsc, vd, vlns in v_cand[cols][:25]:
                    # square-ness: H pitch ~ V pitch (CELLS are roughly square,
                    # even when the grid is rectangular)
                    s_diff = abs(hd - vd) / max(hd, vd)
                    if s_diff > 0.22:
                        continue
                    # full-span gate: every chosen lattice line must cross
                    # essentially the WHOLE grid (>= (dim-0.5)*pitch end to end). H
                    # lines span the grid's HEIGHT (rows*hd); V lines its WIDTH
                    # (cols*vd). A short edge spanning only the central cell (object
                    # in a reference photo, stray texture) is rejected.
                    h_min = (cols - FULL_SPAN_MARGIN) * vd
                    v_min = (rows - FULL_SPAN_MARGIN) * hd
                    if (min(_line_extent(l) for l in hlns) < h_min
                            or min(_line_extent(l) for l in vlns) < v_min):
                        continue
                    alll = hlns + vlns
                    # Colour consistency: ALL gutters of a real grid share one
                    # colour. Photo "grids" mix unrelated edges -> wide spread.
                    ccols = np.array([l.color_lab for l in alll])
                    avg = ccols.mean(axis=0)
                    de = np.sqrt(np.sum((ccols - avg) ** 2, axis=1))
                    if np.max(de) > GRID_COLOR_TOL:
                        continue
                    # angle coherence: H slant ~ -V slant (consistent global tilt)
                    h_ang = np.mean([l.angle for l in hlns])
                    v_ang = np.mean([l.angle for l in vlns])
                    if abs(h_ang + v_ang) > GRID_ANGLE_TOL:
                        continue
                    # cross-axis colour match: H gutters' colour ~ V gutters' colour
                    h_col = np.mean([l.color_lab for l in hlns], axis=0)
                    v_col = np.mean([l.color_lab for l in vlns], axis=0)
                    if _de(h_col, v_col) > XAXIS_COLOR_TOL:
                        continue
                    slant = np.tan(h_ang)
                    ang_inc = abs(h_ang + v_ang) * 200
                    # Prefer the candidate that USES ALL corroborated lines. After
                    # corroboration the surviving lines are all real internal
                    # separators, so the correct grid is the one that incorporates
                    # every one of them — not a sub-set that skips some (which would
                    # under-count, e.g. a 4x4's [r1,r2,r3] dropped to a 3-row
                    # [r2,r3]). Penalise each corroborated line the candidate leaves
                    # UNUSED. (rows-1) H lines and (cols-1) V lines are used.
                    unused = (n_hkeep - (rows - 1)) + (n_vkeep - (cols - 1))
                    # Grid-span fit: the perpendicular gutters run the WHOLE grid and
                    # stop at its outer borders. The grid's extrapolated borders are
                    # one pitch beyond the outer internal lines: H rows occupy
                    # [hpos[0]-hd, hpos[-1]+hd]; V cols [vpos[0]-vd, vpos[-1]+vd]. If
                    # the V gutters extend a FULL CELL past a row border (or H gutters
                    # past a col border), there is an UNCOVERED cell there — the chosen
                    # dimension is too small (a 4-row grid mislabelled 3 rows leaves
                    # the top sky row uncovered while its V gutters still run all 4
                    # cells). Penalise only an uncovered overshoot >= MISSING_LINE_FRAC
                    # of a pitch, so a gutter that merely BLEEDS a little past the grid
                    # (hcaptcha's V gutters reach the submit bar, ~0.65 cell) does NOT
                    # invent a row. This is what lets the completed 4x4 beat the 3-row
                    # subset without over-counting hcaptcha 3x3.
                    hsorted = sorted(hpos); vsorted = sorted(vpos)
                    row_top, row_bot = hsorted[0] - hd, hsorted[-1] + hd
                    col_lft, col_rgt = vsorted[0] - vd, vsorted[-1] + vd
                    vy = [_line_span_perp(l) for l in vlns]        # V gutters' y-extent
                    hx = [_line_span_perp(l) for l in hlns]        # H gutters' x-extent
                    vy_lo = float(np.median([s[0] for s in vy]))
                    vy_hi = float(np.median([s[1] for s in vy]))
                    hx_lo = float(np.median([s[0] for s in hx]))
                    hx_hi = float(np.median([s[1] for s in hx]))
                    # An overshoot only signals a MISSING row/col when it is a real
                    # extra cell, not the gutter colour bleeding into a one-sided
                    # margin / white footer / header bar. The bleed signature is
                    # ASYMMETRY: the gutter runs to the image EDGE on the overshoot
                    # side while its OTHER end stays well inside the image (e.g.
                    # hcaptcha's white footer touches the bottom edge but the grid top
                    # is inset). A grid that genuinely fills an axis reaches BOTH edges
                    # symmetrically (its outer cells are real) — and then has no
                    # overshoot to suppress anyway (its border sits at the edge). So we
                    # suppress only a one-sided edge bleed. EDGE_BLEED_PX absorbs a
                    # 1-2px crop border.
                    v_top_edge = vy_lo <= EDGE_BLEED_PX
                    v_bot_edge = vy_hi >= h - EDGE_BLEED_PX
                    h_lft_edge = hx_lo <= EDGE_BLEED_PX
                    h_rgt_edge = hx_hi >= w - EDGE_BLEED_PX
                    def _missing(uncov, pitch, bleed):
                        if bleed:
                            return 0.0
                        f = uncov / pitch
                        return f if f >= MISSING_LINE_FRAC else 0.0
                    span_pen = SPAN_FIT_PENALTY * (
                        _missing(max(0.0, row_top - vy_lo), hd, v_top_edge and not v_bot_edge)
                        + _missing(max(0.0, vy_hi - row_bot), hd, v_bot_edge and not v_top_edge)
                        + _missing(max(0.0, col_lft - hx_lo), vd, h_lft_edge and not h_rgt_edge)
                        + _missing(max(0.0, hx_hi - col_rgt), vd, h_rgt_edge and not h_lft_edge))
                    score = (hsc + vsc + s_diff * 1000 + abs(slant) * 500
                             + unused * 400 + ang_inc + span_pen)
                    if score < best_score:
                        boxes = _generate_grid(rows, cols, hpos, vpos, hd, vd, h, w, slant)
                        # Cell-content gate IN the loop so a rejected (over-counted)
                        # candidate lets a smaller valid one win, instead of killing
                        # detection outright.
                        if boxes and _cells_have_content(lab, boxes, rows, cols, hlns + vlns):
                            best_score = score
                            best = (boxes, rows, cols, slant, avg,
                                    sorted(hpos), hd, sorted(vpos), vd, hlns + vlns)
    if best is None:
        return None, None, None
    boxes, rows, cols, slant, gutter_color, hpos, hd, vpos, vd, chosen_lns = best
    # Is the CHOSEN grid built from clean painted gutters? If so the lattice is
    # already proven a real grid (a textured-photo FP has noisy pseudo-gutters,
    # std well above the threshold). For such a proven grid we do NOT count a few
    # stray CLEAN full-span lines (a sky horizon, a power line, a UI rule) as
    # off-lattice evidence — they are painted edges in the scene, not extra cell
    # boundaries. FPs keep the strict count because their own gutters are noisy,
    # so this relaxation never applies to them.
    grid_gutters_clean = (float(np.mean([l.color_std for l in chosen_lns]))
                          < CLEAN_GUTTER_STD) if chosen_lns else False
    # Off-lattice gate (the main FP killer for textured photos): within the grid's
    # OWN span, a REAL grid has no extra same-colour lines that break the regular
    # cell spacing — every cell boundary sits ON the lattice (k*pitch from the
    # chosen internal lines). A textured photo (grass, foliage, fences) yields many
    # parallel same-colour edges scattered OFF the lattice. So count same-colour
    # lines that fall OFF the lattice; too many -> photo noise, not a grid.
    #
    # CRITICAL: only consider lines INSIDE the chosen internal-line span
    # (anchors[0]..anchors[-1]). The grid has no reliable outer border, so the
    # real grid's top edge / a UI footer bar can sit a non-pitch distance ABOVE
    # the first internal line or BELOW the last — those are frame/chrome, not
    # evidence the central cells are irregular, and must not count. The central
    # closed region is the only reliable signal (per design).
    def _off_lattice(lines, total, anchors, pitch):
        n = 0
        clean_skipped = 0
        lo, hi = anchors[0], anchors[-1]
        for l in _internal(lines, total):
            if not (lo - LATTICE_TOL * pitch <= l.midline_pos <= hi + LATTICE_TOL * pitch):
                continue                      # outside the central span — frame/chrome
            if _de(l.color_lab, gutter_color) > GRID_COLOR_TOL:
                continue                      # different colour — not a gutter
            # distance to the nearest lattice node (anchor + k*pitch)
            off = min(abs((l.midline_pos - anchors[0]) - round((l.midline_pos - anchors[0]) / pitch) * pitch),
                      abs((l.midline_pos - anchors[-1]) - round((l.midline_pos - anchors[-1]) / pitch) * pitch))
            if off > LATTICE_TOL * pitch:
                # On a proven clean grid we forgive a FEW stray CLEAN full-span lines
                # (a sky horizon, a power line, a UI rule sitting off the lattice) —
                # but only up to MAX_OFF_LATTICE_CLEAN of them. A textured photo whose
                # white-ish edges happen to be clean produces MANY such strays; once
                # they exceed the small allowance we count the rest, so the off-lattice
                # FP gate still fires on texture. Noisy strays always count.
                if (grid_gutters_clean and l.color_std < OFF_LATTICE_CLEAN_STD
                        and clean_skipped < MAX_OFF_LATTICE_CLEAN):
                    clean_skipped += 1
                    continue
                n += 1
        return n
    if (_off_lattice(h_lines, h, hpos, hd) > MAX_OFF_LATTICE
            or _off_lattice(v_lines, w, vpos, vd) > MAX_OFF_LATTICE):
        return None, None, None
    return boxes, (rows, cols), slant


def _detect_grid(image_path, seed_bias=0.0):
    img = cv2.imread(image_path)
    if img is None:
        return None
    h, w = img.shape[:2]
    lab = _to_lab(img)
    h_lines = _trace_lines(lab, axis=1, seed_bias=seed_bias)
    if len(_internal(h_lines, h)) < 2:
        return None
    v_lines = _trace_lines(lab, axis=0, seed_bias=-seed_bias)
    if len(_internal(v_lines, w)) < 2:
        return None
    boxes, dims, slant = extract_grid_from_lines(h_lines, v_lines, h, w, lab=lab)
    return boxes


def find_grid(image_path: str, debug_manager=None, slant_to_try: Optional[float] = None) -> Optional[List[Tuple[int, int, int, int]]]:
    """Main entry point for grid detection (public contract unchanged).

    Detects a 3x3 or 4x4 grid by tracing consistent-colour separator lines of any
    border colour and small tilt. `slant_to_try` is accepted for backward
    compatibility; the tracer recovers slant on its own so it is used only as a
    seed bias hint. Returns row-major cell boxes, or None.
    """
    seed_bias = float(slant_to_try) if slant_to_try is not None else 0.0
    boxes = _detect_grid(image_path, seed_bias=seed_bias)
    if debug_manager and getattr(debug_manager, 'enabled', False) and boxes:
        image_basename = os.path.basename(image_path)
        debug_path = os.path.join(str(getattr(debug_manager, 'base_dir', ".")),
                                  f"grid_final_{image_basename}")
        try:
            get_numbered_grid_overlay(image_path, boxes, output_path=debug_path)
        except Exception:
            pass
    return boxes


def detect_selected_cells(image_path, grid_boxes, debug_manager=None):
    """
    Checks each grid box to see if it contains a 'selected' badge (blue checkmark)
    or a 'loading' spinner.

    - reCAPTCHA puts a small blue badge in the **top-left** of the tile.
    - hCaptcha puts a blue circle-with-check overlay in the **top-right** AND
      darkens the entire tile.

    Returns (list of selected indices, list of loading indices).
    """
    img = cv2.imread(image_path)
    if img is None: return [], []
    sel, ld = [], []
    # OpenCV uses BGR not RGB, so these are swapped from the doc colors.
    recap_blue = (27, 115, 232)   # reCAPTCHA blue badge (#1B73E8)
    hcap_blue = (188, 117, 15)    # hCaptcha blue check  (#0F75BC) in BGR
    for i, box in enumerate(grid_boxes):
        cell = img[box[1]:box[3], box[0]:box[2]]
        if cell.size == 0: continue

        h_cell, w_cell = cell.shape[:2]
        # Top-left (reCAPTCHA).
        tl = cell[0:int(h_cell * 0.4), 0:int(w_cell * 0.4)]
        if tl.size > 0 and _has_badge(tl, recap_blue):
            sel.append(i + 1)
            continue
        # Top-right (hCaptcha). The badge is a small filled blue circle
        # (~10-14 px); _has_badge's circularity check is too strict at that
        # size, so we use a simple color-presence test: "is there a strongly
        # blue-dominant cluster in the top-right corner?"
        tr = cell[0:max(8, int(h_cell * 0.22)), int(w_cell * 0.78):]
        if tr.size > 0 and _has_hcaptcha_check(tr):
            sel.append(i + 1)
            continue

        # Center for loading spinner.
        cntr = cell[int(h_cell * 0.3):int(h_cell * 0.7), int(w_cell * 0.3):int(w_cell * 0.7)]
        if cntr.size > 0 and _is_loading(cntr, recap_blue):
            ld.append(i + 1)
    return sel, ld

# --- per-cell state helpers ---
# These take a 1-indexed `cell_number` (matching detect_selected_cells and the
# grid_boxes[v - 1] click mapping in solver.py) and read pixel values from the
# cropped cell. All guard against a missing image, empty crop, or out-of-range
# index and return a safe default rather than raising.

def _crop_cell(image_path, grid_boxes, cell_number):
    """Load image and return the BGR crop for a 1-indexed cell, or None."""
    img = cv2.imread(image_path)
    if img is None:
        return None
    if cell_number < 1 or cell_number > len(grid_boxes):
        return None
    x1, y1, x2, y2 = grid_boxes[cell_number - 1]
    cell = img[y1:y2, x1:x2]
    return cell if cell.size else None

def is_empty_cell(image_path, grid_boxes, cell_number,
                  white_frac=0.97, l_thresh=92.0, chroma_thresh=6.0):
    """True if the cell is effectively blank: an overwhelming majority of
    pixels are near-white AND near-neutral (low chroma). Uses LAB to match the
    grid-line whiteness test used elsewhere in this module, so a faintly tinted
    "white" still counts while a saturated bright tile (e.g. sky) does not.
    1-indexed cell."""
    cell = _crop_cell(image_path, grid_boxes, cell_number)
    if cell is None:
        return False
    lab = cv2.cvtColor(cell, cv2.COLOR_BGR2LAB).astype(np.float32)
    L = lab[:, :, 0] * (100.0 / 255.0)          # OpenCV packs L into 0..255
    a = lab[:, :, 1] - 128.0
    b = lab[:, :, 2] - 128.0
    chroma = np.sqrt(a * a + b * b)
    white = (L > l_thresh) & (chroma < chroma_thresh)
    return float(white.mean()) >= white_frac

def is_cell_opacity_changing(image_path_a, image_path_b, grid_boxes,
                             cell_number, change_thresh=0.02):
    """True if the cell visibly changed between two frames (still fading/loading).
    Mirrors the absdiff -> gray -> threshold -> ratio approach used by
    check-movement. 1-indexed cell. Returns False if either crop is
    unavailable or the crops differ in shape."""
    a = _crop_cell(image_path_a, grid_boxes, cell_number)
    b = _crop_cell(image_path_b, grid_boxes, cell_number)
    if a is None or b is None or a.shape != b.shape:
        return False
    diff = cv2.absdiff(a, b)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    _, thr = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
    ratio = cv2.countNonZero(thr) / (thr.shape[0] * thr.shape[1])
    return ratio > change_thresh

def wait_for_cell_loaded(frame_paths, grid_boxes, cell_number):
    """Given >=1 chronological frame screenshots, return True once the cell is
    loaded: NOT empty in the latest frame AND (if >=2 frames) NOT changing
    between the last two. Composes is_empty_cell + is_cell_opacity_changing.

    The CLI does not own the browser loop, so this can't do a time-based wait;
    the JS caller captures frames over time and passes the most recent ones.
    The grid_boxes must come from a single reference frame. 1-indexed cell."""
    if not frame_paths:
        return False
    last = frame_paths[-1]
    if is_empty_cell(last, grid_boxes, cell_number):
        return False
    if len(frame_paths) >= 2:
        if is_cell_opacity_changing(frame_paths[-2], last, grid_boxes, cell_number):
            return False
    return True

def is_cell_selected(image_path, grid_boxes, cell_number, debug_manager=None):
    """True if the given 1-indexed cell is selected. Thin wrapper over
    detect_selected_cells (the canonical badge detector) for API symmetry."""
    selected, _ = detect_selected_cells(image_path, grid_boxes, debug_manager)
    return cell_number in selected

def _has_hcaptcha_check(roi):
    """Detect hCaptcha's selected-state blue circle in the top-right corner.

    The badge is a small filled cyan-teal circle (~10-14 px) with a white
    checkmark glyph inside. Naively counting blue-dominant pixels matches
    sky tiles, so we require BOTH:
      - >=8 cyan-teal pixels (B high, G mid-high, R low)
      - >=2 near-white pixels (the checkmark) in the same patch
    """
    if roi is None or roi.size == 0:
        return False
    flat = roi.reshape(-1, 3).astype(np.int32)
    # Teal/cyan: B>120, G>80, R<80, AND B-R gap > 60.
    teal = (
        (flat[:, 0] > 120)
        & (flat[:, 1] > 80)
        & (flat[:, 2] < 80)
        & (flat[:, 0] - flat[:, 2] > 60)
    )
    # Bright white check mark inside the circle.
    white = (flat[:, 0] > 220) & (flat[:, 1] > 220) & (flat[:, 2] > 220)
    return int(teal.sum()) >= 8 and int(white.sum()) >= 2


def _has_badge(roi, rgb):
    """
    Uses color segmentation and shape analysis to detect the reCAPTCHA 
    selection badge (a blue circle/checkmark).
    """
    mask = _create_delta_e_mask(roi, rgb, 8.0)
    # Check if there's enough blue color
    if cv2.countNonZero(mask) <= roi.size * 0.003: return False
    
    # Shape analysis: look for a circular-ish contour
    contours, _ = cv2.findContours(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3,3), np.uint8)), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours: return False
    cnt = max(contours, key=cv2.contourArea)
    hull = cv2.convexHull(cnt)
    area, perim = cv2.contourArea(hull), cv2.arcLength(hull, True)
    
    # Circularity check
    if perim == 0 or (4*np.pi*area/(perim*perim)) < 0.8: return False
    
    # Position check: badge should be in the top-left portion of its ROI
    M = cv2.moments(hull)
    if M["m00"] == 0 or (M["m10"]/M["m00"]) > roi.shape[1]*0.7 or (M["m01"]/M["m00"]) > roi.shape[0]*0.7: return False
    return True

def _is_loading(roi, rgb):
    """
    Detects if a cell is in a 'loading' state based on the amount 
    of blue color in the center.
    """
    mask = _create_delta_e_mask(roi, rgb, 12.0)
    # Loading state usually has a specific range of blue pixels
    return 0.05 < (cv2.countNonZero(mask) / (roi.shape[0]*roi.shape[1])) < 0.6 if roi.size > 0 else False

def _create_delta_e_mask(img, rgb, thr):
    """
    Creates a binary mask of pixels that are within a certain 
    perceptual distance (Delta E) from the target RGB color.
    """
    t_lab = cv2.cvtColor(np.array([[[rgb[2], rgb[1], rgb[0]]]], dtype=np.uint8), cv2.COLOR_BGR2LAB)[0, 0]
    diff = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32) - t_lab.astype(np.float32)
    return (np.sqrt(np.sum(diff**2, axis=2)) <= thr).astype(np.uint8)*255

def get_numbered_grid_overlay(image_path, grid_boxes, output_path=None):
    """
    Generates a debug image with numbered boxes overlaid on the original image.
    Uses high-visibility red labels with white text in the top-right.
    """
    ov = [{"bbox": [b[0], b[1], b[2]-b[0], b[3]-b[1]], "number": i+1, "color": "#FF0000", "box_style": "solid"} for i, b in enumerate(grid_boxes)]
    if output_path is None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf: output_path = tf.name
    add_overlays_to_image(image_path, ov, output_path=output_path, label_position="top-right")
    return output_path
