"""False-positive guard: run find_grid over NON-grid puzzle types. Every one of
these SHOULD return None. Any box returned is a false positive — the gate we are
loosening must not introduce these. Reports FP count + timing per type."""
import os, sys, glob, time, argparse
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import numpy as np
from captchakraken.tool_calls.find_grid import find_grid

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, "..", "..", ".."))
_DATA = os.path.join(_REPO_ROOT, "cleanSamples", "test", "raw")

# Non-grid puzzle types that must NOT yield a grid.
NON_GRID = [
    "hcaptcha_arrows_deviating", "hcaptcha_car_parking_lot",
    "hcaptcha_click_highest_jumper", "hcaptcha_click_image_by_traits",
    "hcaptcha_click_on_path", "hcaptcha_connect_path",
    "hcaptcha_drag_numbered_line_pieces", "hcaptcha_fish_swim_different",
    "hcaptcha_line_ends", "hcaptcha_line_pieces", "hcaptcha_missing_piece",
    "hcaptcha_most_similar_or_different", "hcaptcha_overlapping_lines",
    "hcaptcha_rotating_obj_video", "hcaptcha_semicircle_match",
    "hcaptcha_silhouette_match", "hcaptcha_tetris_fit", "line_connecting_images",
]


def run(limit=0, out=None):
    total = 0; fp = 0; fp_list = []; times = []
    for t in NON_GRID:
        d = os.path.join(_DATA, t)
        paths = sorted(glob.glob(os.path.join(d, "**", "*.png"), recursive=True))
        paths = [p for p in paths if "numbered_overlay" not in p.split(os.sep)]
        if limit: paths = paths[:limit]
        tfp = 0
        for p in paths:
            total += 1
            t0 = time.perf_counter()
            b = find_grid(p)
            times.append((time.perf_counter()-t0)*1000)
            if b is not None:
                fp += 1; tfp += 1
                fp_list.append((os.path.relpath(p, _REPO_ROOT), len(b)))
        if tfp:
            print(f"  FP {t}: {tfp}")
    print(f"FALSE POSITIVES: {fp}/{total}  ({100.0*fp/(total or 1):.2f}%)  "
          f"med={np.median(times):.1f}ms" if times else "no images")
    for p, n in fp_list:
        print(f"    + {n} boxes  {p}")
    return fp, total, fp_list


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    run(a.limit)
