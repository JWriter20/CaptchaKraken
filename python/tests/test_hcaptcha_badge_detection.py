from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

cv2 = pytest.importorskip("cv2")

from captchakraken.tool_calls.find_grid import (
    _has_badge,
    _has_hcaptcha_check,
    detect_selected_cells,
    find_grid,
)

BADGE_BGR = (188, 117, 15)
PATCH = (28, 29)


def _photo_patch(color=(150, 120, 90)) -> np.ndarray:
    return np.full((*PATCH, 3), color, dtype=np.uint8)


def _draw_badge(patch: np.ndarray, diameter: int, style: str) -> np.ndarray:
    out = patch.copy()
    r = diameter // 2
    cx, cy = out.shape[1] - r - 2, r + 2
    cv2.circle(out, (cx, cy), r, BADGE_BGR, -1, cv2.LINE_AA)
    if style == "ringed":
        cv2.circle(out, (cx, cy), r, (255, 255, 255), max(1, diameter // 8), cv2.LINE_AA)
        s, o = max(1, diameter // 7), max(1, r // 2)
        cv2.line(out, (cx - o, cy - o), (cx + o, cy + o), (255, 255, 255), s, cv2.LINE_AA)
        cv2.line(out, (cx - o, cy + o), (cx + o, cy - o), (255, 255, 255), s, cv2.LINE_AA)
    else:
        s = max(1, diameter // 6)
        cv2.line(out, (cx - r // 2, cy), (cx - r // 6, cy + r // 2), (255, 255, 255), s, cv2.LINE_AA)
        cv2.line(out, (cx - r // 6, cy + r // 2), (cx + r // 2, cy - r // 2), (255, 255, 255), s, cv2.LINE_AA)
    return out


@pytest.mark.parametrize("style", ["filled", "ringed"])
@pytest.mark.parametrize("diameter", [10, 12, 14, 16])
def test_a_real_mark_is_still_found(style, diameter):
    assert _has_hcaptcha_check(_draw_badge(_photo_patch(), diameter, style)) is True


def test_sky_over_here_and_something_white_over_there_is_not_a_mark():
    patch = _photo_patch()
    patch[:, :16] = (210, 140, 60)
    patch[:, 24:] = (240, 240, 240)
    assert _has_hcaptcha_check(patch) is False


def test_teal_and_white_that_merely_share_a_corner_is_not_a_mark():
    patch = _photo_patch()
    patch[0:6, 0:6] = (200, 130, 40)
    patch[20:, 20:] = (255, 255, 255)
    assert _has_hcaptcha_check(patch) is False


def test_the_recaptcha_corner_chip_is_unaffected():
    patch = np.full((52, 52, 3), (150, 120, 90), dtype=np.uint8)
    cv2.circle(patch, (12, 12), 11, (232, 115, 27), -1, cv2.LINE_AA)
    assert _has_badge(patch, (27, 115, 232)) is True
    assert _has_badge(_photo_patch(), (27, 115, 232)) is False


_CORPUS = Path(os.environ.get("CAPTCHA_GRID_CORPUS") or "/nonexistent")
_PHANTOM_BUDGET = 0.012


@pytest.mark.skipif(not _CORPUS.is_dir(), reason=f"grid corpus not present at {_CORPUS}")
def test_fresh_boards_report_almost_no_selections():
    tiles = phantoms = 0
    offenders = []
    for path in sorted(_CORPUS.glob("*.png")):
        boxes = find_grid(str(path))
        if not boxes:
            continue
        selected, _ = detect_selected_cells(str(path), boxes)
        tiles += len(boxes)
        phantoms += len(selected)
        if selected:
            offenders.append(f"{path.name}: {selected}")
    if not tiles:
        pytest.skip("no grid detected anywhere in the corpus")
    rate = phantoms / tiles
    assert rate <= _PHANTOM_BUDGET, (
        f"{phantoms} phantom selections over {tiles} tiles of fresh boards "
        f"({rate:.2%} > {_PHANTOM_BUDGET:.2%}). Each one is a tile the solver "
        f"will refuse to click:\n  " + "\n  ".join(offenders)
    )
