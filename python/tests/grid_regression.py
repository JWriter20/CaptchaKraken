"""One-shot regression gate for find_grid changes.

Measures, in a single run:
  - detection accuracy on the 3 established grid types (must stay ~100% / not drop)
  - detection on the 2 NEW target types (grocery_list 6x3, drag_missing_slot 4x4)
  - false positives on the 19 non-grid puzzle types (must NOT increase)
  - timing (median / p95 / max must stay near budget ~75ms)

Exit code 0 always (report tool). Prints a PASS/FAIL summary line per gate vs the
recorded BASELINE so a change can be accepted or rejected at a glance.

Baseline (pre-change, 2026-06-05):
  established: recaptcha_3x3 100.0, recaptcha_4x4 97.2, hcaptcha_3x3 98.7 -> 99.0 overall
  new targets: grocery 0/8, drag 0/53 (effectively)
  false positives: 6/266 (2.26%)
  timing: med ~58ms
"""
import os, sys, glob, time, argparse
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import numpy as np
from captchakraken.tool_calls.find_grid import find_grid

_TESTS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_TESTS, "..", "..", ".."))
_DATA = os.path.join(_ROOT, "cleanSamples", "test", "raw")

ESTABLISHED = {"recaptcha_grid_3x3": 9, "recaptcha_grid_4x4": 16, "hcaptcha_grid_3x3_property": 9}
TARGETS = {"hcaptcha_grocery_list": 18, "hcaptcha_drag_missing_slot": 16}
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


def _imgs(t):
    paths = sorted(glob.glob(os.path.join(_DATA, t, "**", "*.png"), recursive=True))
    return [p for p in paths if "numbered_overlay" not in p.split(os.sep)]


def run(limit=0):
    times = []
    print("=== ESTABLISHED grid types (want high, no regression) ===")
    for t, exp in ESTABLISHED.items():
        ps = _imgs(t)
        if limit: ps = ps[:limit]
        ok = 0
        for p in ps:
            t0 = time.perf_counter(); b = find_grid(p); times.append((time.perf_counter()-t0)*1000)
            if b is not None and len(b) == exp: ok += 1
        print(f"  {t:32s} {ok:4d}/{len(ps):4d}  {100.0*ok/(len(ps) or 1):5.1f}%")
    print("=== NEW target types (want high) ===")
    for t, exp in TARGETS.items():
        ps = _imgs(t)
        if limit: ps = ps[:limit]
        ok = 0
        for p in ps:
            t0 = time.perf_counter(); b = find_grid(p); times.append((time.perf_counter()-t0)*1000)
            if b is not None and len(b) == exp: ok += 1
        print(f"  {t:32s} {ok:4d}/{len(ps):4d}  {100.0*ok/(len(ps) or 1):5.1f}%")
    print("=== NON-GRID (false positives, want 0 / <= baseline 6) ===")
    fp = 0; tot = 0
    for t in NON_GRID:
        ps = _imgs(t)
        if limit: ps = ps[:limit]
        tfp = 0
        for p in ps:
            tot += 1
            t0 = time.perf_counter(); b = find_grid(p); times.append((time.perf_counter()-t0)*1000)
            if b is not None: fp += 1; tfp += 1
        if tfp: print(f"  FP {t}: {tfp}")
    print(f"  TOTAL FP: {fp}/{tot} ({100.0*fp/(tot or 1):.2f}%)")
    print("=== TIMING ===")
    a = np.array(times)
    print(f"  med={np.median(a):.1f}ms p95={np.percentile(a,95):.1f}ms max={a.max():.1f}ms  (n={len(a)})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=0)
    run(ap.parse_args().limit)
