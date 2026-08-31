"""
Benchmark `find_grid` against the full CaptchaKrakenFinetune grid corpus.

Reads the known reCAPTCHA and hCaptcha grid puzzles from
`cleanSamples/test/raw/<grid_type>/`, runs `find_grid` over each, and reports a
per-type detection rate plus a breakdown of wrong-dimension / undetected cases.

This is a REPORT-ONLY benchmark: it always passes (so it never blocks CI on the
heuristic detector's misses), but prints a detailed table so regressions in
detection accuracy are visible in the test output. Use it as a measurement tool.

Run:
    pytest tests/test_find_grid_corpus.py -s          # see the report
    FIND_GRID_SAMPLE=0 pytest tests/test_find_grid_corpus.py -s   # whole corpus
    FIND_GRID_SAMPLE=30 pytest tests/test_find_grid_corpus.py -s  # 30 imgs/type

Env knobs:
    FIND_GRID_SAMPLE  Max images per puzzle type (default 60). 0 = no cap (all).
    FIND_GRID_DATA    Override the cleanSamples/test/raw directory.
"""

import os
import sys
import glob

import pytest

# Add src to path (mirrors the other tests in this dir).
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captchakraken.tool_calls.find_grid import find_grid  # noqa: E402

# ── Dataset location ────────────────────────────────────────────────────────
# The python port lives at .../CaptchaKraken/python, and (in the dev monorepo)
# the dataset at .../CaptchaKrakenFinetune/cleanSamples/test/raw. Walk up 3 levels
# to the finetune root: tests -> python -> CaptchaKraken -> CaptchaKrakenFinetune.
# (Standalone clones won't have the corpus; the test skips.)
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, "..", "..", ".."))
_DEFAULT_DATA = os.path.join(_REPO_ROOT, "cleanSamples", "test", "raw")
DATA_DIR = os.environ.get("FIND_GRID_DATA", _DEFAULT_DATA)

# Expected dimensions per grid puzzle type, mirroring _GRID_DIMS_FROM_PT in
# src/testing/grade.py. (rows, cols) -> expected number of find_grid boxes.
# All of these are uniform-gutter grids handled by the consistent-colour line
# tracer. (The hCaptcha "card grid" widgets — grocery_list / drag_missing_slot —
# relied on a separate template detector that has been removed; find_grid is now
# the line tracer only, so those types are out of scope for this benchmark.)
GRID_TYPES = {
    "recaptcha_grid_3x3": (3, 3),
    "recaptcha_grid_4x4": (4, 4),
    "hcaptcha_grid_3x3_property": (3, 3),
    "geetest_v4_nine": (3, 3),
    "prosopo_grid_3x3": (3, 3),
}


def _sample_limit() -> int:
    raw = os.environ.get("FIND_GRID_SAMPLE", "60")
    try:
        return max(0, int(raw))
    except ValueError:
        return 60


def _images_for_type(grid_type: str) -> list:
    """RAW PNGs under cleanSamples/test/raw/<grid_type>/ (recursive — the
    hcaptcha_grid_3x3_property type nests reference_image/ + standard_text_prompt/).
    Sorted for determinism, then capped by FIND_GRID_SAMPLE.

    Excludes numbered_overlay/ images: those already have the grid lines + cell
    numbers PAINTED ON (they are find_grid's OUTPUT, rendered for the model to
    read). find_grid runs only on raw, un-annotated frames in production; feeding
    it an overlay means detecting a grid in an image whose real gutters have been
    drawn over, which it correctly rejects. Testing those would measure the wrong
    input. (grade.py prefers overlays when handing images to the MODEL — a separate
    concern from grid detection.)"""
    type_dir = os.path.join(DATA_DIR, grid_type)
    paths = sorted(glob.glob(os.path.join(type_dir, "**", "*.png"), recursive=True))
    paths = [p for p in paths if "numbered_overlay" not in p.split(os.sep)]
    limit = _sample_limit()
    if limit:
        paths = paths[:limit]
    return paths


def _build_corpus() -> dict:
    return {gt: _images_for_type(gt) for gt in GRID_TYPES}


def test_find_grid_corpus_report():
    """Run find_grid over the grid corpus and print a per-type accuracy report."""
    if not os.path.isdir(DATA_DIR):
        pytest.skip(f"Grid dataset not found at {DATA_DIR}")

    corpus = _build_corpus()
    total_imgs = sum(len(v) for v in corpus.values())
    if total_imgs == 0:
        pytest.skip(f"No grid images found under {DATA_DIR}")

    limit = _sample_limit()
    print("\n")
    print("=" * 72)
    print("find_grid corpus benchmark")
    print(f"  data dir: {DATA_DIR}")
    print(f"  sample/type: {'ALL' if limit == 0 else limit}")
    print("=" * 72)

    grand = {"total": 0, "correct": 0, "wrong_dim": 0, "none": 0, "bad_shape": 0}

    for grid_type, (rows, cols) in GRID_TYPES.items():
        expected_n = rows * cols
        images = corpus[grid_type]

        stats = {"total": 0, "correct": 0, "wrong_dim": 0, "none": 0, "bad_shape": 0}
        wrong_dim_samples = []

        for path in images:
            stats["total"] += 1
            try:
                boxes = find_grid(path)
            except Exception as e:  # a detector crash counts as a miss, not a test failure
                boxes = None
                if len(wrong_dim_samples) < 5:
                    wrong_dim_samples.append(f"{os.path.basename(path)} (ERROR: {e})")

            if boxes is None:
                stats["none"] += 1
                continue

            n = len(boxes)
            if n != expected_n:
                stats["wrong_dim"] += 1
                if len(wrong_dim_samples) < 5:
                    wrong_dim_samples.append(f"{os.path.basename(path)} -> {n} boxes")
                continue

            # Right count: sanity-check box shape/ordering so a "correct" count
            # with garbage boxes doesn't silently pass.
            if not _boxes_well_formed(boxes):
                stats["bad_shape"] += 1
                continue

            stats["correct"] += 1

        _print_type_report(grid_type, expected_n, stats, wrong_dim_samples)

        for k in grand:
            grand[k] += stats[k]

    print("-" * 72)
    _print_rate("OVERALL", grand)
    print("=" * 72)

    # Report-only: never fail. Surface a clear note if nothing detected at all,
    # which usually means a path/data problem rather than detector accuracy.
    assert grand["total"] > 0
    if grand["correct"] == 0:
        print(
            "\nNOTE: find_grid detected ZERO correct grids across the sampled "
            "corpus. This is more likely a data-path or environment issue than "
            "a detector regression — verify FIND_GRID_DATA points at "
            "cleanSamples/test/raw."
        )


def _boxes_well_formed(boxes) -> bool:
    """Each box is (x1, y1, x2, y2) with x1<x2, y1<y2, non-negative; and cell
    sizes are roughly uniform (within 25% of the mean) so we don't count a
    degenerate detection as correct."""
    widths, heights = [], []
    for box in boxes:
        if len(box) != 4:
            return False
        x1, y1, x2, y2 = box
        if not (x1 < x2 and y1 < y2 and x1 >= 0 and y1 >= 0):
            return False
        widths.append(x2 - x1)
        heights.append(y2 - y1)

    aw = sum(widths) / len(widths)
    ah = sum(heights) / len(heights)
    if aw <= 0 or ah <= 0:
        return False
    for w, h in zip(widths, heights):
        if not (0.75 * aw <= w <= 1.25 * aw):
            return False
        if not (0.75 * ah <= h <= 1.25 * ah):
            return False
    return True


def _print_type_report(grid_type, expected_n, stats, wrong_dim_samples):
    print(f"\n{grid_type}  (expect {expected_n} cells)")
    _print_rate("  ", stats)
    if wrong_dim_samples:
        print("  e.g. mis-detections:")
        for s in wrong_dim_samples:
            print(f"     - {s}")


def _print_rate(label, stats):
    total = stats["total"] or 1
    rate = 100.0 * stats["correct"] / total
    print(
        f"{label:<28} n={stats['total']:>4}  "
        f"correct={stats['correct']:>4} ({rate:5.1f}%)  "
        f"wrong_dim={stats['wrong_dim']:>3}  "
        f"none={stats['none']:>4}  bad_shape={stats['bad_shape']:>3}"
    )


# Regression: geetest_v4_nine panels put the 3x3 tile block between a prompt bar
# and a button footer, so the tracer sees the grid's BOTTOM border as a third
# horizontal line while the top border falls outside the seed band. The bottom
# border is short (it stops where the dark corner tile swallows it), which used to
# kill every 3-column pairing on the full-span gate and leave only half-pitch
# lattices invented by sub-pitch completion — a 5x4 of quarter-size cells crammed
# into the lower middle of the panel. Pin the real 3x3 on the two samples that
# exhibited it. See _axis_candidates: shorter prefixes of an even run are
# candidates in their own right.
_GEETEST_NINE_REGRESSIONS = [
    "geetest_v4_nine_1785265732695_4rn7i.png",
    "geetest_v4_nine_1785265732727_781l3.png",
]


@pytest.mark.parametrize("name", _GEETEST_NINE_REGRESSIONS)
def test_geetest_v4_nine_bordered_panel_is_3x3(name):
    """The grid must be the 9 real tiles, not a half-pitch lattice in a corner."""
    path = os.path.join(DATA_DIR, "geetest_v4_nine", name)
    if not os.path.isfile(path):
        pytest.skip(f"corpus sample not found: {path}")

    boxes = find_grid(path)
    assert boxes is not None and len(boxes) == 9, (
        f"expected a 3x3 grid for {name}, got "
        f"{len(boxes) if boxes else 0} boxes"
    )
    assert _boxes_well_formed(boxes)

    # The 9 cells must actually be the tile block: a real 3x3 panel grid fills
    # most of the frame. The half-pitch mis-detection covered ~60% of the width
    # and sat well below centre, so a coverage floor pins the failure mode even
    # if some other lattice ever produces 9 boxes.
    import cv2

    h, w = cv2.imread(path).shape[:2]
    x0 = min(b[0] for b in boxes); x1 = max(b[2] for b in boxes)
    y0 = min(b[1] for b in boxes); y1 = max(b[3] for b in boxes)
    assert (x1 - x0) >= 0.85 * w, f"grid spans only {x1 - x0}px of {w}px width"
    assert (y1 - y0) >= 0.6 * h, f"grid spans only {y1 - y0}px of {h}px height"


# Regression: real captures the consistent-colour tracer alone could not read,
# recovered by the colour comb + seal cue. Two shapes, both "the gutter is there and
# the walk cannot follow it":
#   * reCAPTCHA 4x4s over open SKY — the gutters are pure white (L 100.0) against sky
#     at L 95-97, so every near-white column seeds a trace of its own and the cluster
#     average lands off the separator (rrv7m's x=103/200/297 read back as 66/96/215);
#   * hCaptcha 3x3s of white-background ARTWORK — the gutter and the tile background
#     are the same white, so along the horizontal axis there is no local boundary at
#     all and only the lattice's repetition gives it away.
# These were 12 of the 15 misses across the whole 1423-image grid corpus.
_LOW_CONTRAST_REGRESSIONS = [
    ("recaptcha_grid_4x4/recaptcha_1775818840074_rrv7m.png", 16),
    ("recaptcha_grid_4x4/recaptcha_1776682819458_5rr89.png", 16),
    ("recaptcha_grid_4x4/recaptcha_1780052442679_wdcnb.png", 16),
    ("recaptcha_grid_4x4/recaptcha_1776250807779_ttvu9.png", 16),
    ("recaptcha_grid_3x3/fade_out/recaptcha_1774782032623_h023s__fade.png", 9),
    ("hcaptcha_grid_3x3_property/reference_image/hcaptcha_images_cow4.png", 9),
    ("hcaptcha_grid_3x3_property/reference_image/hcaptcha_images_cow12.png", 9),
    ("hcaptcha_grid_3x3_property/reference_image/hcaptcha_1774782019474_clz0d.png", 9),
    ("hcaptcha_grid_3x3_property/standard_text_prompt/hcaptcha_images_fruit4.png", 9),
    ("hcaptcha_grid_3x3_property/standard_text_prompt/hcaptcha_images_icy_drink3.png", 9),
    ("hcaptcha_grid_3x3_property/standard_text_prompt/hcaptcha_1786104013550_omnx9.png", 9),
    ("hcaptcha_grid_3x3_property/standard_text_prompt/hcaptcha_1787659293920_ymfa0.png", 9),
]


@pytest.mark.parametrize("rel,expected", _LOW_CONTRAST_REGRESSIONS)
def test_low_contrast_gutters_are_detected(rel, expected):
    """These real captures must produce their full grid, via the default sweep."""
    path = os.path.join(DATA_DIR, *rel.split("/"))
    if not os.path.isfile(path):
        pytest.skip(f"corpus sample not found: {path}")

    boxes = find_grid(path)
    assert boxes is not None and len(boxes) == expected, (
        f"expected {expected} cells for {rel}, got "
        f"{len(boxes) if boxes else 0} — the comb/seal cue has regressed"
    )
    assert _boxes_well_formed(boxes)


if __name__ == "__main__":
    # Allow running directly: python tests/test_find_grid_corpus.py
    test_find_grid_corpus_report()
