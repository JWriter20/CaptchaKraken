"""Instrument extract_grid_from_lines to report WHY a grid was rejected.

For one image, prints:
  - how many internal H/V lines were traced (stage A/B: line finding)
  - the corroboration survivors per axis
  - per (rows,cols) candidate: which gate killed it (square / full-span / color
    spread / angle / cross-axis color / cell-content / off-lattice)

This separates the two diagnostic questions the user posed:
  1. did we find the lines that make up the grid?  (H_int / V_int / corroborated)
  2. did we extract the right grid from the lattice?  (which gate rejected)
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import cv2
from captchakraken.tool_calls import find_grid as FG


def analyze(image_path):
    img = cv2.imread(image_path)
    h, w = img.shape[:2]
    lab = FG._to_lab(img)
    h_lines = FG._trace_lines(lab, axis=1, seed_bias=0.0)
    v_lines = FG._trace_lines(lab, axis=0, seed_bias=0.0)
    print(f"\n=== {os.path.basename(image_path)}  ({w}x{h}) ===")
    print(f"traced internal: H={len(FG._internal(h_lines,h))}  V={len(FG._internal(v_lines,w))}")
    for ax, lines, total in (("H", h_lines, h), ("V", v_lines, w)):
        ipos = sorted(round(l.midline_pos, 1) for l in FG._internal(lines, total))
        print(f"  {ax} internal midlines: {ipos}")

    h_keep = FG._corroborate(h_lines, v_lines, h)
    v_keep = FG._corroborate(v_lines, h_lines, w)
    n_hkeep = len(FG._internal(h_keep, h))
    n_vkeep = len(FG._internal(v_keep, w))
    print(f"corroborated: H={n_hkeep} V={n_vkeep}")
    print(f"  H keep midlines: {sorted(round(l.midline_pos,1) for l in FG._internal(h_keep,h))}")
    print(f"  V keep midlines: {sorted(round(l.midline_pos,1) for l in FG._internal(v_keep,w))}")

    h_cand = FG._axis_candidates(h_keep, h)
    v_cand = FG._axis_candidates(v_keep, w)
    print(f"axis candidates: rows-dims={sorted(h_cand)}  cols-dims={sorted(v_cand)}")

    # replicate the extraction loop with reject reasons
    reasons = {}
    def bump(r): reasons[r] = reasons.get(r, 0) + 1
    found = []
    for rows in sorted(h_cand):
        if rows < FG.MIN_GRID_DIM: continue
        for cols in sorted(v_cand):
            if cols < FG.MIN_GRID_DIM: continue
            for hpos, hsc, hd, hlns in h_cand[rows][:25]:
                for vpos, vsc, vd, vlns in v_cand[cols][:25]:
                    s_diff = abs(hd - vd) / max(hd, vd)
                    if s_diff > 0.22: bump("square"); continue
                    h_min = (cols - FG.FULL_SPAN_MARGIN) * vd
                    v_min = (rows - FG.FULL_SPAN_MARGIN) * hd
                    if (min(FG._line_extent(l) for l in hlns) < h_min
                            or min(FG._line_extent(l) for l in vlns) < v_min):
                        bump("full_span"); continue
                    alll = hlns + vlns
                    ccols = np.array([l.color_lab for l in alll])
                    avg = ccols.mean(axis=0)
                    de = np.sqrt(np.sum((ccols - avg) ** 2, axis=1))
                    if np.max(de) > FG.GRID_COLOR_TOL: bump("color_spread"); continue
                    h_ang = np.mean([l.angle for l in hlns])
                    v_ang = np.mean([l.angle for l in vlns])
                    if abs(h_ang + v_ang) > FG.GRID_ANGLE_TOL: bump("angle"); continue
                    h_col = np.mean([l.color_lab for l in hlns], axis=0)
                    v_col = np.mean([l.color_lab for l in vlns], axis=0)
                    if FG._de(h_col, v_col) > FG.XAXIS_COLOR_TOL: bump("xaxis_color"); continue
                    slant = np.tan(h_ang)
                    boxes = FG._generate_grid(rows, cols, hpos, vpos, hd, vd, h, w, slant)
                    if not boxes: bump("gen_grid_none"); continue
                    if not FG._cells_have_content(lab, boxes, rows, cols, hlns + vlns):
                        # show the divergence values
                        gc = np.mean([l.color_lab for l in (hlns+vlns)], axis=0)
                        divs = FG._cell_divergences(lab, boxes, gc)
                        ndiv = sum(1 for d in divs if d > FG.CELL_DIVERGE_TOL)
                        bump(f"cell_content({rows}x{cols}:{ndiv}/{len(divs)})"); continue
                    found.append((rows, cols))
    print(f"reject reasons: {reasons}")
    print(f"survived extraction loop (pre off-lattice): {found}")
    boxes, dims, slant = FG.extract_grid_from_lines(h_lines, v_lines, h, w, lab=lab)
    print(f"FINAL: dims={dims} boxes={len(boxes) if boxes else 0}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        analyze(p)
