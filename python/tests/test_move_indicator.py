"""Tests for the hCaptcha "Move" draggable-indicator detector.

`find_move_indicators` finds the dark teal "[⊹] Move" pill that caps each
draggable card in hCaptcha drag puzzles. The EXPECTED_COUNTS below are the
user-validated ground truth on the golden cleanSamples/test/raw store; the
NO_INDICATOR cases assert the detector stays quiet on grid / click puzzles
(which never carry a Move pill) — these are the corpus's main false-positive
risk (white numbered overlays, the Skip/FINISH labels).

Images live in the parent repo's cleanSamples/test/raw (the CLI is a submodule);
tests skip cleanly if that tree isn't present.
"""

import os

import cv2
import pytest

from captchakraken.tool_calls.move_indicator import (
    find_movable_content,
    find_move_indicators,
)

# tests/ -> python -> CaptchaKraken -> CaptchaKrakenFinetune (dev monorepo)
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
RAW = os.path.join(_REPO_ROOT, "cleanSamples", "test", "raw")


def _p(rel: str) -> str:
    return os.path.join(RAW, rel)


# (relative path, expected number of Move pills) — user-validated ground truth.
EXPECTED_COUNTS = [
    ("hcaptcha_missing_piece/animals_and_items_halves/hcaptcha_drag_movable_tractor3_select_1.png", 2),
    ("hcaptcha_missing_piece/animals_parts_scattered/hcaptcha_drag_movable_deer_antlers_select_2.png", 2),
    ("hcaptcha_drag_missing_slot/sprites/hcaptcha_1772489530900_ezbqm.png", 2),
    ("hcaptcha_drag_missing_slot/shapes/hcaptcha_1775905220880_lkhb8.png", 2),
    ("hcaptcha_drag_numbered_line_pieces/hcaptcha_1772489516691_ss62w.png", 2),
    ("hcaptcha_fish_swim_different/hcaptcha_1772411727618_fow06.png", 1),
    ("hcaptcha_drag_missing_slot/symbols/hcaptcha_1776855620191_sjqd9.png", 1),
    ("hcaptcha_connect_path/hcaptcha_1773140410328_i7gyd.png", 3),
    ("hcaptcha_grocery_list/hcaptcha_1778238016353_9mm49.png", 0),
    # tetris_fit always stacks 3 candidate pieces, each with a Move pill —
    # including the faint-glyph variant that an earlier glyph gate dropped.
    ("hcaptcha_tetris_fit/hcaptcha_1775214020284_bb5q8.png", 3),
    ("hcaptcha_tetris_fit/hcaptcha_1775127629276_b979n.png", 3),
    # silhouette_match: central_match has NO Move pill (click puzzle); the three
    # drag variants each do.
    ("hcaptcha_silhouette_match/central_match/hcaptcha_1772798415913_dnsig.png", 0),
    ("hcaptcha_silhouette_match/drag_letter/hcaptcha_1773658819208_huvle.png", 1),
    ("hcaptcha_silhouette_match/drag_over_silhouette/hcaptcha_1772798409543_07zd8.png", 2),
    # Dark side-panel pills the wordmark-first detector dropped (faint / sliced by
    # a thin horizontal artifact); recovered by the teal-body-first path. The
    # third pill in each of these sits on the dark gray card column.
    ("hcaptcha_connect_path/hcaptcha_1777978814231_utwm0.png", 3),
    ("hcaptcha_connect_path/hcaptcha_1778382380816_rkwuq.png", 3),
    ("hcaptcha_drag_numbered_line_pieces/hcaptcha_1773745219780_r45bo.png", 2),
    # A 2-pill connect_path layout (not every connect_path has 3).
    ("hcaptcha_connect_path/hcaptcha_1776250810480_yp460.png", 2),
    # Mis-filed under drag_missing_slot/symbols but actually the "tile that changes
    # letter" CLICK puzzle (E/E/Z/U) — no Move pill exists, so the answer is 0.
    ("hcaptcha_drag_missing_slot/symbols/hcaptcha_1778151620213_w3o98.png", 0),
]

# Click / grid puzzles that carry NO Move pill — must stay at 0 (FP guard).
# These include the four reCAPTCHA photo grids that the wordmark-first detector
# false-positived on (a dark photo region medianing teal); the teal-body path's
# tighter flat-fill gate drives them to 0.
NO_INDICATOR = [
    "recaptcha_grid_4x4/numbered_overlay/recaptcha_1767563318182_sieip.png",
    "hcaptcha_grid_3x3_property/standard_text_prompt/hcaptcha_1773140411302_zfp1d.png",
    "recaptcha_grid_3x3/numbered_overlay/recaptcha_1775386820986_maqn5.png",
    "recaptcha_grid_3x3/numbered_overlay/recaptcha_1778929254320_dcpk5.png",
    "recaptcha_grid_4x4/recaptcha_1772884814225_9amok.png",
    "recaptcha_grid_4x4/recaptcha_1777978807781_po3pp.png",
]


def _require(path: str):
    if not os.path.exists(path):
        pytest.skip(f"golden image not present: {path}")


@pytest.mark.parametrize("rel,expected", EXPECTED_COUNTS)
def test_move_indicator_count(rel, expected):
    path = _p(rel)
    _require(path)
    im = cv2.imread(path)
    assert im is not None, f"failed to read {path}"
    boxes = find_move_indicators(im)
    assert len(boxes) == expected, (
        f"{rel}: expected {expected} Move indicator(s), got {len(boxes)}: {boxes}"
    )


@pytest.mark.parametrize("rel", NO_INDICATOR)
def test_no_indicator_on_grid_puzzles(rel):
    path = _p(rel)
    _require(path)
    im = cv2.imread(path)
    assert im is not None, f"failed to read {path}"
    boxes = find_move_indicators(im)
    assert boxes == [], f"{rel}: grid/click puzzle should have no Move pill, got {boxes}"


# Folder-level invariants the user validated: every image in these folders has
# the same Move-pill count.
FOLDER_COUNTS = [
    ("hcaptcha_tetris_fit", 3),                       # 3 candidate pieces each
    ("hcaptcha_silhouette_match/central_match", 0),   # click puzzle, no pill
]


@pytest.mark.parametrize("folder,expected", FOLDER_COUNTS)
def test_folder_indicator_count(folder, expected):
    import glob

    d = os.path.join(RAW, folder)
    if not os.path.isdir(d):
        pytest.skip(f"folder not present: {d}")
    imgs = sorted(glob.glob(os.path.join(d, "*.png")))
    if not imgs:
        pytest.skip(f"no images in {d}")
    bad = []
    for path in imgs:
        im = cv2.imread(path)
        if im is None:
            continue
        n = len(find_move_indicators(im))
        if n != expected:
            bad.append((os.path.basename(path), n))
    assert not bad, f"{folder}: expected {expected} everywhere, off in: {bad}"


def test_indicator_boxes_are_valid():
    """Every returned box is a positive-area rect inside the image."""
    path = _p("hcaptcha_connect_path/hcaptcha_1773140410328_i7gyd.png")
    _require(path)
    im = cv2.imread(path)
    H, W = im.shape[:2]
    for x, y, w, h in find_move_indicators(im):
        assert w > 0 and h > 0
        assert 0 <= x and 0 <= y and x + w <= W and y + h <= H


def test_indicator_detection_is_deterministic():
    path = _p("hcaptcha_missing_piece/animals_parts_scattered/hcaptcha_drag_movable_deer_antlers_select_2.png")
    _require(path)
    im = cv2.imread(path)
    first = find_move_indicators(im)
    for _ in range(3):
        assert find_move_indicators(im) == first


def test_find_movable_content_below_each_pill():
    """Each detected pill yields a content box that sits BELOW it and overlaps
    its horizontal span (the movable card the pill caps)."""
    path = _p("hcaptcha_missing_piece/animals_parts_scattered/hcaptcha_drag_movable_deer_antlers_select_2.png")
    _require(path)
    im = cv2.imread(path)
    inds = find_move_indicators(im)
    assert inds, "expected at least one Move pill"
    for x, y, w, h in inds:
        content = find_movable_content(im, [x, y, w, h])
        assert content is not None, f"no movable content under pill {[x, y, w, h]}"
        cx, cy, cw, ch = content
        # content starts at or below the pill bottom
        assert cy >= y, f"content {content} not below pill {[x, y, w, h]}"
        # content's horizontal span overlaps the pill's
        assert cx < x + w and cx + cw > x, f"content {content} not under pill {[x, y, w, h]}"
        assert cw > 0 and ch > 0
