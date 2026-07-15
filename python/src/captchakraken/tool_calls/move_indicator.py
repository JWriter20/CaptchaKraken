"""Detect the hCaptcha "Move" draggable indicator and the movable object below it.

TEAL-BODY-FIRST redesign. hCaptcha drag-style puzzles (connect_path,
drag_missing_slot, missing_piece, tetris_fit, fish_swim_different,
drag_numbered_line_pieces, ...) stack one or more draggable CARDS, each topped by
a dark rounded "Move" pill: a four-arrow glyph (⊹) plus the white word "Move".

This module gives two pure-OpenCV steps (no GPU, no network):

  1. find_move_indicators(im)        -> [x, y, w, h] of every Move pill
  2. find_movable_content(im, pill)  -> the card / object box BELOW one pill

Detection strategy (step 1) — TEAL BODY FIRST. The pill BODY is a fixed UI
element rendered as a flat dark desaturated TEAL: HSV hue ~108 (BGR ~(56,45,38),
channels ordered B>G>R), saturation ~82, value ~56. This colour is BYTE-STABLE
and opacity-robust: it stays teal even when the white text is faint, fragmented,
or sliced by a thin horizontal artifact (the failure mode of a wordmark-first
detector on pills embedded in a dark side-panel).

So we:

  * Build a mask of pill-teal pixels (hue [103,113], S>=40, V in [35,95]),
    close it into solid horizontal blobs, and take connected components with
    pill GEOMETRY (rounded rectangle: w 50-175, h 16-46, aspect 2.0-7.5, and a
    high-extent FILLED rectangle). This catches pills on light cards AND dark
    panels AND artifact-sliced pills, because the body colour is robust.
  * CONFIRM each teal candidate is a real Move pill by requiring SOME white
    wordmark evidence inside the body (a minimum count of bright low-saturation
    pixels spread horizontally across the interior). Because candidate generation
    is teal-locked, confirmation can be LENIENT on text faintness without
    exploding false positives — the only competitors are textured photographic
    tiles, whose bright pixels are sparse and clustered, not a spread wordmark.
  * UNION with the legacy wordmark-first path (for any pill the teal mask misses)
    and dedup overlapping boxes.

Post-filters: a vertical band [0.12H, 0.87H] (excludes the bottom "Skip" button
and top chrome) and IoU/center dedup. Byte-deterministic per image.
"""

from __future__ import annotations

from typing import List, Optional

import cv2
import numpy as np

# ── Move-pill body colour ────────────────────────────────────────────────────
# Flat dark desaturated TEAL: HSV hue locked at ~108, BGR ordered B>G>R with a
# tight characteristic spacing (B-G≈11, G-R≈7). Byte-stable across hundreds of
# real pill bodies; photographic tiles scatter across hue space and break the
# B>G>R ordering.
_PILL_HUE = 108

# Geometry / threshold constants (px on the ~520-wide hCaptcha canvas; gates are
# structural, not absolute coordinates, so they hold at other sizes).
_BRIGHT_V = 150        # min HSV V for "white ink" (glyph + text)
_BRIGHT_S = 90         # max HSV S for "white ink"
_DARK_V = 110          # below this V is "dark pill body"
_Y_BAND = (0.12, 0.87)  # keep pills inside the puzzle body; excludes the bottom
#                         "Skip" button and top chrome.

# ── teal candidate-generation params ─────────────────────────────────────────
_TEAL_HUE_LO, _TEAL_HUE_HI = 103, 113   # pill teal hue window (±5 of 108)
_TEAL_S_MIN = 40                          # pill body saturation floor
_TEAL_V_LO, _TEAL_V_HI = 35, 95           # pill body value window
_TEAL_W = (50, 175)                       # pill body width range (px)
_TEAL_H = (16, 46)                        # pill body height range (px)
_TEAL_AR = (2.0, 7.5)                     # pill aspect ratio (w/h)
_TEAL_EXTENT = 0.80                       # filled-rectangle floor (area / w·h)

# ── confirmation params (white wordmark evidence inside the teal body) ───────
# Measured separation on the corpus:
#   real pills      : bright_frac 0.08-0.16, bright_px 160-190, hspan ≥ ~0.5·w
#                     (even content-blank/faint/sliced cases clear these)
#   FP teal tiles   : bright_frac ≤ 0.04, bright_px ≤ 76, hspan small/fragmented
# We require a spread (hspan) AND a mass (frac) of bright ink, which lenient on
# faintness but rejects the sparse, clustered bright pixels of photo texture.
_CONF_BRIGHT_FRAC = 0.055   # min bright-pixel fraction inside the body
_CONF_BRIGHT_PX = 80        # min absolute bright-pixel count inside the body
_CONF_HSPAN = 0.45          # bright ink must span ≥ this fraction of body width


def find_move_indicators(im: np.ndarray) -> List[List[int]]:
    """Return ``[x, y, w, h]`` boxes (pixels) of every hCaptcha "Move" pill in the
    BGR image ``im``. Empty list when there is none (e.g. grid / click puzzles).
    """
    H, W = im.shape[:2]

    # Teal-body-first candidates (the primary path), UNION the legacy wordmark
    # path (recovers anything the teal mask misses), then dedup.
    cands = _teal_body_pills(im)
    cands.extend(_wordmark_runs(im))

    y_lo, y_hi = _Y_BAND[0] * H, _Y_BAND[1] * H
    cands = [c for c in cands if y_lo <= c[1] + c[3] / 2 <= y_hi]

    merged: List[List[int]] = []
    for c in sorted(cands, key=lambda c: -c[2] * c[3]):
        if all(not _centers_overlap(c, m) for m in merged):
            merged.append(c)
    # stable ordering: top-to-bottom, then left-to-right
    merged.sort(key=lambda c: (c[1], c[0]))
    return merged


def find_movable_content(
    im: np.ndarray,
    indicator: List[int],
    *,
    pad: int = 4,
) -> Optional[List[int]]:
    """Given one Move pill ``indicator`` (``[x, y, w, h]``), return the bounding
    box ``[x, y, w, h]`` of the movable CARD/object that sits directly BELOW it.

    The pill caps the top of a draggable card. We walk down from just under the
    pill, gathering the contiguous run of card rows (rows that contain real,
    non-background content) until we hit a gap (the margin before the next card
    or the panel edge), and bound it horizontally to the column the pill heads.
    Pure OpenCV; returns None if no content is found under the pill.
    """
    H, W = im.shape[:2]
    px, py, pw, ph = indicator
    gray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)

    # Horizontal search window: centred on the pill, a bit wider (cards are
    # usually a touch wider than their pill), clamped to the frame.
    cx = px + pw / 2
    half = max(pw, 70) * 0.75
    sx0 = int(max(0, cx - half))
    sx1 = int(min(W, cx + half))

    # Start just below the pill; scan downward.
    top = min(H - 1, py + ph + 1)

    # Per-row "has content" signal: a row inside a card has internal structure
    # (edges) and/or a spread of intensities; a margin/gap row is flat.
    band = gray[:, sx0:sx1]
    row_std = band.std(axis=1)
    # Robust background estimate from the rows immediately under the pill margin.
    gap_thresh = max(6.0, float(np.median(row_std)) * 0.5)

    # Find the contiguous content run starting at/after `top`.
    y = top
    # skip an initial thin margin (up to ~14px) between pill and card body
    skip = 0
    while y < H and row_std[y] < gap_thresh and skip < 16:
        y += 1
        skip += 1
    content_start = y
    run_gap = 0
    last_content = content_start
    while y < H:
        if row_std[y] >= gap_thresh:
            last_content = y
            run_gap = 0
        else:
            run_gap += 1
            if run_gap >= 10:  # a real gap ends this card
                break
        y += 1
    if last_content <= content_start:
        return None

    y0, y1 = content_start, last_content + 1

    # Tighten horizontally to the actual content columns within the run.
    sub = gray[y0:y1, sx0:sx1]
    col_std = sub.std(axis=0)
    cthr = max(6.0, float(np.median(col_std)) * 0.5)
    cols = np.where(col_std >= cthr)[0]
    if cols.size:
        cx0 = sx0 + int(cols.min())
        cx1 = sx0 + int(cols.max()) + 1
    else:
        cx0, cx1 = sx0, sx1

    bx = max(0, cx0 - pad)
    by = max(0, y0 - pad)
    bw = min(W - bx, (cx1 - cx0) + 2 * pad)
    bh = min(H - by, (y1 - y0) + 2 * pad)
    if bw < 12 or bh < 12:
        return None
    return [int(bx), int(by), int(bw), int(bh)]


# ── internals ────────────────────────────────────────────────────────────────


def _teal_body_pills(im: np.ndarray) -> List[List[int]]:
    """PRIMARY path: find pills directly by their flat-teal BODY, then confirm
    each with white-wordmark evidence inside.

    Candidate generation is teal-locked (opacity-robust, survives faint/sliced
    text), so it catches the dark-side-panel pills a wordmark-first detector
    drops. Confirmation is lenient on text faintness because the only things that
    can pass the teal+geometry gate are real pills and the rare photographic tile
    that happens to median teal — and those have sparse, clustered bright pixels,
    not a spread wordmark.
    """
    H, W = im.shape[:2]
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    Hh = hsv[:, :, 0]
    S = hsv[:, :, 1]
    V = hsv[:, :, 2]

    mask = (
        (Hh >= _TEAL_HUE_LO)
        & (Hh <= _TEAL_HUE_HI)
        & (S >= _TEAL_S_MIN)
        & (V >= _TEAL_V_LO)
        & (V <= _TEAL_V_HI)
    ).astype(np.uint8)

    # Close into solid horizontal blobs (bridge the white glyph/text gaps inside
    # the body and any thin artifact slice through it).
    closed = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3)),
    )

    n, lbl, stats, _ = cv2.connectedComponentsWithStats(closed)
    out: List[List[int]] = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if not (_TEAL_W[0] <= w <= _TEAL_W[1] and _TEAL_H[0] <= h <= _TEAL_H[1]):
            continue
        ar = w / max(h, 1)
        if not (_TEAL_AR[0] <= ar <= _TEAL_AR[1]):
            continue
        if a / float(w * h) < _TEAL_EXTENT:  # must be a FILLED rounded rectangle
            continue
        if not _wordmark_inside(im, x, y, w, h):
            continue
        # Emit a box padded to span the full pill (the teal blob is the inner
        # body; the rounded corners + a little margin extend slightly beyond).
        bx = max(0, x - 4)
        by = max(0, y - 4)
        bw = min(W - bx, w + 8)
        bh = min(H - by, h + 8)
        out.append([int(bx), int(by), int(bw), int(bh)])
    return out


def _wordmark_inside(im: np.ndarray, x: int, y: int, w: int, h: int) -> bool:
    """CONFIRMATION: require white-wordmark evidence inside the teal body.

    A real pill carries "[glyph] Move" — bright (V>150, S<90) ink spread
    horizontally across the body interior. Photographic teal tiles have only
    sparse, clustered bright pixels. We gate on (a) enough bright mass (count and
    fraction) and (b) horizontal spread, all measured inside the candidate body.
    """
    sub = im[y:y + h, x:x + w]
    if sub.size == 0:
        return False
    hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
    bright = (hsv[:, :, 2] > _BRIGHT_V) & (hsv[:, :, 1] < _BRIGHT_S)
    bcount = int(bright.sum())
    if bcount < _CONF_BRIGHT_PX:
        return False
    if bright.mean() < _CONF_BRIGHT_FRAC:
        return False
    cols = np.where(bright.sum(axis=0) > 0)[0]
    if cols.size == 0:
        return False
    span = cols.max() - cols.min() + 1
    if span < _CONF_HSPAN * w:
        return False
    return True


def _wordmark_runs(im: np.ndarray) -> List[List[int]]:
    """LEGACY (union) path: find Move pills by their white "[glyph] Move"
    wordmark, then gate each on a dark pill body, a left glyph cluster, and the
    flat-teal pill body colour. Recovers any pill the teal-body path misses."""
    H, W = im.shape[:2]
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    V = hsv[:, :, 2]
    S = hsv[:, :, 1]
    bright = ((V > _BRIGHT_V) & (S < _BRIGHT_S)).astype(np.uint8)

    # Strip large solid bright blobs (cards / shapes / instruction text); keep the
    # thin glyph + letter strokes. This makes the wordmark pop whether the pill is
    # on a light card or inside a dark side-panel.
    op = cv2.morphologyEx(
        bright, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    )
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(op)
    bigsolid = np.zeros_like(bright)
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a > 400 and (w > 40 or h > 22):
            bigsolid[lbl == i] = 1
    bigsolid = cv2.dilate(bigsolid, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    textonly = cv2.subtract(bright, bigsolid)

    # Close strokes horizontally into "[glyph] Move" wordmark blobs.
    closed = cv2.morphologyEx(
        textonly * 255,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (23, 3)),
    )
    nn, ll, ss, _ = cv2.connectedComponentsWithStats(closed)
    out: List[List[int]] = []
    for i in range(1, nn):
        x, y, w, h, a = ss[i]
        ar = w / max(h, 1)
        if not (2.8 < ar < 13 and 5 <= h <= 22 and 30 <= w <= 130):
            continue
        sub = V[y:y + h, x:x + w]
        tmask = textonly[y:y + h, x:x + w] > 0
        body = sub[~tmask]
        if body.size == 0:
            continue
        if np.median(body) > 85:          # pill body must be dark
            continue
        if (body < 100).mean() < 0.45:    # solidly dark, not a stray run
            continue
        if not _glyph_present(bright, x, y, w, h, H, W):
            continue
        if not _pill_body_match(im, x, y, w, h):
            continue
        bx = max(0, x - 30)
        by = max(0, y - 9)
        bw = min(W - bx, w + 50)
        bh = min(H - by, h + 20)
        out.append([int(bx), int(by), int(bw), int(bh)])
    return out


def _glyph_present(
    bright: np.ndarray, x: int, y: int, w: int, h: int, H: int, W: int
) -> bool:
    """The move glyph (four-arrow symbol) is a compact bright cluster immediately
    LEFT of "Move". Require a bright cluster in the left ~40% of the wordmark.
    "FINISH" / "Skip" labels have NO bright pixels there, so they fail.

    The glyph renders at varying opacity: when crisp it's a tall plus/cross that
    spans most of the text height; when faint only its vertical stroke + arrow
    tips clear the brightness threshold. So accept either a tall-enough cluster
    OR a smaller cluster with enough concentrated bright mass — both clearly
    distinguish a glyph from "no glyph at all"."""
    gy0, gy1 = max(0, y - 3), min(H, y + h + 3)
    gx0, gx1 = max(0, x - 7), min(W, x + w)
    sub = bright[gy0:gy1, gx0:gx1]
    if sub.size == 0:
        return False
    sh, sw = sub.shape
    lw = max(5, int(sw * 0.40))
    left = sub[:, :lw]
    rows = np.where(left.sum(axis=1) > 0)[0]
    if rows.size == 0:
        return False
    vext = rows.max() - rows.min() + 1
    mass = int(left.sum())
    if vext >= 0.35 * sh and mass >= 6:
        return True
    if mass >= 10 and vext >= 0.25 * sh:
        return True
    return False


def _pill_body_match(im: np.ndarray, x: int, y: int, w: int, h: int) -> bool:
    """True iff the dark body inside the wordmark bbox is the flat teal Move pill.

    Trims padding to a central core, takes the dark (non-text) pixels, and gates
    on: (1) median hue within 4 of the pill teal (108); (2) BGR channel ordering
    B>G>R with the pill's spacing (B-G in [5,15], G-R in [3,13]); (3) saturation
    >= 40; (4) flat fill — grayscale std <= 24 AND hue std <= 14 (a photo tile
    that coincidentally medians teal is still textured, so its hue/gray std blow
    past these). Models the rendered UI element, not coordinates."""
    tx = int(w * 0.15)
    ty = int(h * 0.12)
    x0, x1 = x + tx, x + w - tx
    y0, y1 = y + ty, y + h - ty
    if x1 - x0 < 10 or y1 - y0 < 6:
        x0, x1, y0, y1 = x, x + w, y, y + h
    crop = im[y0:y1, x0:x1]
    if crop.size == 0:
        return False
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    Hh = hsv[:, :, 0].astype(np.float32)
    V = hsv[:, :, 2].astype(np.float32)
    S = hsv[:, :, 1].astype(np.float32)
    bright = (V > _BRIGHT_V) & (S < _BRIGHT_S)
    dark = (V < _DARK_V) & (~bright)
    if dark.sum() < 15:
        return False
    px = crop.reshape(-1, 3).astype(np.float32)[dark.reshape(-1)]
    b, g, r = np.median(px, axis=0)
    hue_med = float(np.median(Hh[dark]))
    sat_med = float(np.median(S[dark]))
    if abs(hue_med - _PILL_HUE) > 4:                  # body must be the pill teal
        return False
    if not (5 <= b - g <= 15 and 3 <= g - r <= 13):   # B>G>R, pill channel spacing
        return False
    if sat_med < 40:
        return False
    gray_std = float(px.mean(axis=1).std())
    hue_std = float(Hh[dark].std())
    # Flat fill, not photo texture. The pill body is a single rendered teal, so
    # its hue is essentially constant (hue_std ≤ ~1.5 across every real pill);
    # the corpus's coincidental-teal photo tiles, even when they median teal, are
    # textured and scatter in hue (hue_std 3-44). hue_std ≤ 2 removes all four
    # legacy false positives while keeping every real legacy-only recovery.
    if gray_std > 24 or hue_std > 2.0:
        return False
    return True


def _iou(a: List[int], b: List[int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix0, iy0 = max(ax, bx), max(ay, by)
    ix1, iy1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    return 0.0 if inter == 0 else inter / (aw * ah + bw * bh - inter)


def _centers_overlap(a: List[int], b: List[int]) -> bool:
    """Dedup test robust to differently-sized boxes of the same pill: True if
    IoU>=0.3 OR either box's center lies inside the other."""
    if _iou(a, b) >= 0.3:
        return True
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    acx, acy = ax + aw / 2, ay + ah / 2
    bcx, bcy = bx + bw / 2, by + bh / 2
    return (bx <= acx <= bx + bw and by <= acy <= by + bh) or (
        ax <= bcx <= ax + aw and ay <= bcy <= ay + ah
    )
