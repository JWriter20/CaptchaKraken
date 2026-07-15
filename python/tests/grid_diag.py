"""Grid-detection diagnostic harness.

Runs the production find_grid pipeline over the full test corpus, measuring:
  - detection rate per type (correct / wrong_dim / none / bad_shape)
  - per-image latency (to guard the ~75ms/img budget)
  - for FAILURES, renders a debug overlay showing the traced h/v lines and the
    final grid (if any) so the cause can be diagnosed by eye.

It reaches INTO find_grid internals (_to_lab, _trace_lines, extract_grid_from_lines)
so we can see *what lines were traced* vs *what grid was extracted* — the two
stages the user asked to diagnose separately.

Usage:
    python tests/grid_diag.py                       # default types, all images
    python tests/grid_diag.py --types recaptcha_grid_3x3 hcaptcha_grid_3x3_property
    python tests/grid_diag.py --overlay-failures    # write debug PNGs for misses
    python tests/grid_diag.py --overlay-all         # write debug PNGs for everything
    python tests/grid_diag.py --limit 50            # cap images per type
    python tests/grid_diag.py --out /tmp/grid_diag  # overlay output dir
"""
import os
import sys
import glob
import time
import argparse
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import cv2
import numpy as np

from captchakraken.tool_calls import find_grid as FG
from captchakraken.tool_calls.find_grid import (
    find_grid, _to_lab, _trace_lines, extract_grid_from_lines, _internal,
)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, "..", "..", ".."))
_DEFAULT_DATA = os.path.join(_REPO_ROOT, "cleanSamples", "test", "raw")

# Expected (rows, cols) per type. None = "is a grid but dimension not fixed /
# not scored on dimension" (used for the new target dirs whose exact layout we
# are still characterising).
DEFAULT_DIMS = {
    "recaptcha_grid_3x3": (3, 3),
    "recaptcha_grid_4x4": (4, 4),
    "hcaptcha_grid_3x3_property": (3, 3),
}
# Target dirs to EXTEND coverage to. Dimensions filled in after we inspect them.
TARGET_DIMS = {
    "hcaptcha_grocery_list": None,
    "hcaptcha_drag_missing_slot": None,
}


def images_for_type(data_dir, grid_type, limit=0):
    type_dir = os.path.join(data_dir, grid_type)
    paths = sorted(glob.glob(os.path.join(type_dir, "**", "*.png"), recursive=True))
    paths = [p for p in paths if "numbered_overlay" not in p.split(os.sep)]
    if limit:
        paths = paths[:limit]
    return paths


def detect_with_internals(image_path, seed_bias=0.0):
    """Replicates _detect_grid but returns the intermediate lines too."""
    img = cv2.imread(image_path)
    if img is None:
        return None, [], [], None
    h, w = img.shape[:2]
    lab = _to_lab(img)
    h_lines = _trace_lines(lab, axis=1, seed_bias=seed_bias)
    v_lines = _trace_lines(lab, axis=0, seed_bias=-seed_bias)
    boxes = None
    dims = None
    if len(_internal(h_lines, h)) >= 2 and len(_internal(v_lines, w)) >= 2:
        boxes, dims, slant = extract_grid_from_lines(h_lines, v_lines, h, w, lab=lab)
    return boxes, h_lines, v_lines, dims


def draw_debug(image_path, h_lines, v_lines, boxes, dims, out_path):
    """Overlay traced lines (h=red, v=blue) and final grid boxes (green) onto the
    raw image, with the box count annotated. Lets us SEE both diagnostic stages."""
    img = cv2.imread(image_path)
    if img is None:
        return
    vis = img.copy()
    H, W = vis.shape[:2]

    def line_pts(l):
        return (int(round(l.start[0])), int(round(l.start[1]))), \
               (int(round(l.end[0])), int(round(l.end[1])))

    # traced lines — full extent of each fit
    for l in h_lines:
        p0, p1 = line_pts(l)
        internal = H * 0.02 < l.midline_pos < H * 0.98
        col = (0, 0, 255) if internal else (0, 100, 200)
        cv2.line(vis, p0, p1, col, 1, cv2.LINE_AA)
    for l in v_lines:
        p0, p1 = line_pts(l)
        internal = W * 0.02 < l.midline_pos < W * 0.98
        col = (255, 0, 0) if internal else (200, 100, 0)
        cv2.line(vis, p0, p1, col, 1, cv2.LINE_AA)

    # final grid boxes
    if boxes:
        for i, (x1, y1, x2, y2) in enumerate(boxes):
            cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 2)

    nh = len(_internal(h_lines, H))
    nv = len(_internal(v_lines, W))
    label = f"H_int={nh} V_int={nv} boxes={len(boxes) if boxes else 0} dims={dims}"
    cv2.rectangle(vis, (0, 0), (W, 22), (0, 0, 0), -1)
    cv2.putText(vis, label, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(out_path, vis)


def classify(boxes, expected):
    """expected = (rows, cols) or None. Returns one of correct/wrong_dim/none."""
    if boxes is None:
        return "none"
    n = len(boxes)
    if expected is None:
        return "correct" if n >= 9 else "wrong_dim"
    er, ec = expected
    return "correct" if n == er * ec else "wrong_dim"


def run(args):
    dims = dict(DEFAULT_DIMS)
    if args.include_targets:
        dims.update(TARGET_DIMS)
    if args.types:
        dims = {t: dims.get(t) for t in args.types}

    os.makedirs(args.out, exist_ok=True)
    grand = {"total": 0, "correct": 0, "wrong_dim": 0, "none": 0}
    all_times = []
    report = {}

    for gtype, expected in dims.items():
        paths = images_for_type(args.data, gtype, args.limit)
        st = {"total": 0, "correct": 0, "wrong_dim": 0, "none": 0}
        fails = []
        times = []
        for p in paths:
            st["total"] += 1
            t0 = time.perf_counter()
            try:
                boxes, h_lines, v_lines, d = detect_with_internals(p)
            except Exception as e:
                boxes, h_lines, v_lines, d = None, [], [], None
                fails.append((p, f"ERROR {e}"))
            dt = (time.perf_counter() - t0) * 1000.0
            times.append(dt); all_times.append(dt)
            cls = classify(boxes, expected)
            st[cls] += 1
            if cls != "correct":
                fails.append((p, f"{cls} boxes={len(boxes) if boxes else 0}"))
                if args.overlay_failures or args.overlay_all:
                    name = f"{gtype}__{os.path.basename(p)}"
                    draw_debug(p, h_lines, v_lines, boxes, d, os.path.join(args.out, name))
            elif args.overlay_all:
                name = f"OK__{gtype}__{os.path.basename(p)}"
                draw_debug(p, h_lines, v_lines, boxes, d, os.path.join(args.out, name))
        for k in grand:
            grand[k] += st[k]
        rate = 100.0 * st["correct"] / (st["total"] or 1)
        tmed = float(np.median(times)) if times else 0.0
        tp95 = float(np.percentile(times, 95)) if times else 0.0
        print(f"{gtype:32s} n={st['total']:4d} correct={st['correct']:4d} "
              f"({rate:5.1f}%) wrong_dim={st['wrong_dim']:3d} none={st['none']:4d} "
              f"| med={tmed:5.1f}ms p95={tp95:5.1f}ms")
        report[gtype] = {"stats": st, "fails": [(os.path.relpath(p, _REPO_ROOT), r) for p, r in fails],
                         "med_ms": tmed, "p95_ms": tp95}

    rate = 100.0 * grand["correct"] / (grand["total"] or 1)
    print("-" * 100)
    print(f"{'OVERALL':32s} n={grand['total']:4d} correct={grand['correct']:4d} "
          f"({rate:5.1f}%) wrong_dim={grand['wrong_dim']:3d} none={grand['none']:4d} "
          f"| med={np.median(all_times):5.1f}ms p95={np.percentile(all_times,95):5.1f}ms "
          f"max={np.max(all_times):5.1f}ms")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2)
        print(f"[wrote] {args.json}")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=_DEFAULT_DATA)
    ap.add_argument("--types", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--overlay-failures", action="store_true")
    ap.add_argument("--overlay-all", action="store_true")
    ap.add_argument("--include-targets", action="store_true")
    ap.add_argument("--out", default="/tmp/grid_diag")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    run(args)
