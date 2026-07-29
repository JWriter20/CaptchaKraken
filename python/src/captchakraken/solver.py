"""
CaptchaSolver (v2) — vLLM-backed.

Flow:
  1. find_grid → if a grid is detected, draw the numbered overlay, ask the
     `captcha` LoRA which cells to click, return ClickActions with
     per-tile bounding boxes.
  2. find_checkbox on small images → if a lone checkbox is detected, return a
     ClickAction targeting it directly.
  3. Otherwise (click / drag / video puzzle) → route the MEDIA to the
     full-puzzle pixel/action path (planner.get_pixel_actions), which the LoRA
     is trained on. Only raise UnsupportedCaptchaError if the model returns
     nothing usable.

Video challenges are solved, not skipped. A clip reaches the model as a clip:
the OpenCV steps take its first frame because they need an array of pixels, but
the model query gets the mp4, because for types like "select the object with a
unique motion pattern" every frame looks identical and only the motion differs.

v1 had a SAM3-backed tool-using planner with detect/segment/drag-refine; it
lives on the `v1-old-architecture` branch.
"""

import math
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

from PIL import Image

from .action_types import (
    CaptchaAction,
    ClickAction,
    DoneAction,
    DragAction,
    WaitAction,
)
from .image_processor import ImageProcessor
from .planner import VIDEO_EXTS, ActionPlanner
from .timing import timed
from .tool_calls.find_checkbox import find_checkbox
from .tool_calls.find_grid import (
    detect_selected_cells, find_grid, get_numbered_grid_overlay,
)

DEBUG = os.getenv("CAPTCHA_DEBUG", "0") == "1"

# Half-size (0–1 fraction) of the click/drag target box built around each point
# the model returns. The TS solver clicks the box center, so this only sets how
# much positional slack executeClick has; ~1.2% ≈ ±6px on a 512px challenge.
_PIXEL_BOX_HALF = float(os.getenv("CAPTCHA_PIXEL_BOX_HALF", "0.012"))


class UnsupportedCaptchaError(Exception):
    """Raised when the captcha is neither a supported grid nor a checkbox."""


class DebugManager:
    """Writes per-run artifacts under `latestDebugRun/` when CAPTCHA_DEBUG=1."""

    def __init__(self, debug_enabled: bool):
        self.enabled = debug_enabled
        self.base_dir = Path("latestDebugRun").resolve()
        self.log_file = self.base_dir / "log.txt"
        if self.enabled:
            self._setup_dir()

    def _setup_dir(self):
        if self.base_dir.exists():
            try:
                shutil.rmtree(self.base_dir)
            except Exception as e:
                print(f"[DebugManager] Warning: Could not clear debug dir: {e}", file=sys.stderr)
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "w") as f:
                f.write(f"Debug Run Started: {datetime.now()}\n")
        except Exception as e:
            print(f"[DebugManager] Error creating debug dir: {e}", file=sys.stderr)

    def log(self, message: str):
        if self.enabled:
            print(f"[DEBUG] {message}", file=sys.stderr)
            try:
                with open(self.log_file, "a") as f:
                    f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")
            except Exception:
                pass
        elif DEBUG:
            print(f"[Solver] {message}", file=sys.stderr)

    def save_image(self, image_path: str, name: str) -> Optional[str]:
        if not self.enabled:
            return None
        if not self.base_dir.exists():
            self._setup_dir()
        target = self.base_dir / name
        try:
            shutil.copy2(image_path, target)
            self.log(f"Saved image: {name}")
            return str(target)
        except Exception as e:
            self.log(f"Failed to save image {name}: {e}")
            return None


class CaptchaSolver:
    """v2 solver: OpenCV grid detection + vLLM `captcha` LoRA."""

    def __init__(
        self,
        model: Optional[str] = None,
        provider: str = "captchaKrakenApi",
        api_key: Optional[str] = None,
    ):
        self.debug = DebugManager(DEBUG)
        # `provider` kept for argv compatibility with the v1 CLI signature.
        if provider not in {"captchaKrakenApi"}:
            self.debug.log(f"Provider {provider!r} ignored; v2 only supports captchaKrakenApi.")
        self.planner = ActionPlanner(
            model=model, api_key=api_key, debug_callback=self.debug.log
        )
        self.image_processor = ImageProcessor(None, self.planner, self.debug)
        self._image_size: Optional[Tuple[int, int]] = None
        self._temp_files: List[str] = []

    def __del__(self):
        for f in self._temp_files:
            if os.path.exists(f):
                try:
                    os.unlink(f)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def solve(
        self,
        media_path: str,
        instruction: str = "",
        puzzle_source: str = "unknown",
        retry_mode: Optional[str] = None,
    ) -> Union[CaptchaAction, List[CaptchaAction]]:
        media_path = str(Path(media_path).resolve())
        if not os.path.exists(media_path):
            raise FileNotFoundError(f"Media not found: {media_path}")

        # Two paths on purpose. The OpenCV steps below (grid detection, checkbox
        # detection, the real-grid sanity check) only work on a still, so they
        # get one frame. The MODEL gets the original media: for an animated
        # challenge the motion IS the puzzle — hCaptcha's "unique motion pattern"
        # shows several identical objects that differ only in how they spin —
        # and collapsing that to frame 0 destroys the only signal there is.
        cv_image_path = self._materialize_image(media_path)
        model_media_path = media_path if self._is_video(media_path) else cv_image_path
        self.debug.save_image(cv_image_path, "00_base_image.png")
        assert self._image_size is not None
        img_w, img_h = self._image_size

        with timed("solver.find_grid"):
            grid_boxes = find_grid(cv_image_path)
        if grid_boxes and self._is_real_grid(cv_image_path, grid_boxes):
            self.debug.log(f"Detected grid with {len(grid_boxes)} cells")
            # Grids stay on the still: the model is asked to pick cells off a
            # NUMBERED OVERLAY, which can only be drawn on one frame, and no
            # grid captcha in the corpus needs motion to be read.
            return self._solve_grid(cv_image_path, grid_boxes, retry_mode=retry_mode)
        elif grid_boxes:
            # find_grid latched onto e.g. an hCaptcha click-puzzle's
            # header/footer bands. Reject — only true grids are supported.
            self.debug.log(
                f"find_grid returned {len(grid_boxes)} cells but failed the "
                "real-grid sanity check."
            )

        if img_h < 400:
            with timed("solver.find_checkbox"):
                checkbox = find_checkbox(cv_image_path)
            if checkbox:
                self.debug.log(f"Detected checkbox at {checkbox}")
                x, y, w, h = checkbox
                return ClickAction(
                    action="click",
                    target_bounding_boxes=[
                        [x / img_w, y / img_h, (x + w) / img_w, (y + h) / img_h]
                    ],
                )

        # Not a grid or checkbox → a click/drag/pixel puzzle. The full-puzzle
        # LoRA is trained on all of these, including the video types, so route
        # the media (clip when there is one) to the pixel action path.
        actions = self._solve_pixel(model_media_path)
        if actions:
            return actions

        # The model returned nothing usable (blank/unrendered frame or a truly
        # un-actionable image). Surface as unsupported so the caller fails fast
        # instead of clicking nothing.
        raise UnsupportedCaptchaError("Cannot solve this kind of captcha")

    def _solve_pixel(
        self, image_path: str
    ) -> List[Union[ClickAction, DragAction]]:
        """Turn the model's normalized 0–1 click/drag actions into ClickAction /
        DragAction bboxes. Each point becomes a small box centered on it (the TS
        solver clicks the box center)."""
        raw_actions = self.planner.get_pixel_actions(image_path)
        R = _PIXEL_BOX_HALF
        clamp = lambda v: min(max(v, 0.0), 1.0)

        def box(cx: float, cy: float) -> List[float]:
            return [clamp(cx - R), clamp(cy - R), clamp(cx + R), clamp(cy + R)]

        out: List[Union[ClickAction, DragAction]] = []
        for a in raw_actions:
            if a.get("kind") == "click":
                boxes = [box(x, y) for (x, y) in a.get("points", [])]
                if boxes:
                    out.append(
                        ClickAction(action="click", target_bounding_boxes=boxes)
                    )
            elif a.get("kind") == "drag":
                sx, sy = a["src"]
                dx, dy = a["dst"]
                out.append(
                    DragAction(
                        action="drag",
                        source_bounding_box=box(sx, sy),
                        target_bounding_box=box(dx, dy),
                    )
                )
        return out

    # Back-compat alias.
    def solveVideo(self, *args, **kwargs):
        return self.solve(*args, **kwargs)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _is_video(media_path: str) -> bool:
        return media_path.lower().endswith(VIDEO_EXTS)

    def _materialize_image(self, media_path: str) -> str:
        """A static PNG for the OpenCV steps (first frame, for a clip).

        This is NOT what the model sees for a video — see `solve()`. It exists
        only because grid/checkbox detection needs a single array of pixels.
        """
        if self._is_video(media_path):
            import cv2

            cap = cv2.VideoCapture(media_path)
            ok, frame = cap.read()
            cap.release()
            if not ok:
                raise ValueError(f"Could not read video frame from {media_path}")
            self._image_size = (frame.shape[1], frame.shape[0])
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                cv2.imwrite(tf.name, frame)
                self._temp_files.append(tf.name)
                return tf.name

        with Image.open(media_path) as img:
            self._image_size = img.size
        return media_path

    def _is_real_grid(
        self,
        image_path: str,
        grid_boxes: List[Tuple[int, int, int, int]],
    ) -> bool:
        """Reject candidates that look like click-puzzle false positives.

        A real captcha grid has 9 or 16 photographic tiles. Each tile is
        independent imagery so the per-cell color stddev is *all* high.

        hCaptcha click puzzles look like a 3-row stack (header band /
        center image / footer band). find_grid sometimes interprets the
        band edges as 2 horizontal grid lines × 2 spurious vertical lines,
        producing 9 "cells" where the top/bottom rows are mostly a single
        flat color (the band). Filter on that.
        """
        try:
            import cv2
            import numpy as np

            img = cv2.imread(image_path)
            if img is None:
                return True  # Be permissive on read failure.

            # Per-cell mean color standard deviation. Photo tiles ~ 30-80.
            # Flat color band rows ~ 0-12.
            stds: List[float] = []
            for (x1, y1, x2, y2) in grid_boxes:
                if x2 <= x1 or y2 <= y1:
                    continue
                roi = img[y1:y2, x1:x2]
                if roi.size == 0:
                    continue
                # mean of per-channel stddev — robust to monochrome cells.
                stds.append(float(np.mean(np.std(roi.reshape(-1, 3), axis=0))))

            if not stds:
                return False

            n = len(grid_boxes)
            side = 3 if n == 9 else 4 if n == 16 else int(round(n ** 0.5))
            rows = [stds[i * side:(i + 1) * side] for i in range(side)]
            cols = [stds[i::side] for i in range(side)]

            self.debug.log(
                f"_is_real_grid: per-cell stddev = {[round(s, 1) for s in stds]}"
            )

            # If the *whole* image is mostly flat (e.g. screenshotting the
            # tiny hCaptcha anchor iframe with just the "I'm not a robot"
            # text), find_grid hallucinates a 9-cell grid. Reject early.
            import statistics
            if statistics.mean(stds) < 25.0:
                self.debug.log(
                    f"_is_real_grid: overall mean stddev "
                    f"{statistics.mean(stds):.1f} too low → reject."
                )
                return False

            FLAT = 20.0
            # Build a side×side flat-mask (1 = flat cell, 0 = rich cell).
            flat_mask = [[1 if stds[r * side + c] < FLAT else 0
                          for c in range(side)] for r in range(side)]
            flat_count = sum(sum(row) for row in flat_mask)

            # Heuristic: in a *real* captcha grid, flat tiles (sky, asphalt)
            # always form a spatially contiguous blob (top-left sky stack,
            # bottom road row, etc.) because the underlying image is a single
            # photo split into tiles. In a click-puzzle false grid, the flat
            # cells are the **left + right margins** of the middle row,
            # separated by the rich photo cell — non-contiguous.
            #
            # Use 4-connected components on flat cells. If we get more than
            # one component with >=1 cell each, it's the click-puzzle pattern.
            def components(mask):
                seen = [[False] * side for _ in range(side)]
                comps = 0
                for r in range(side):
                    for c in range(side):
                        if mask[r][c] and not seen[r][c]:
                            comps += 1
                            stack = [(r, c)]
                            while stack:
                                y, x = stack.pop()
                                if seen[y][x] or not mask[y][x]:
                                    continue
                                seen[y][x] = True
                                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                                    ny, nx = y + dy, x + dx
                                    if 0 <= ny < side and 0 <= nx < side:
                                        stack.append((ny, nx))
                return comps

            if flat_count >= 2 and components(flat_mask) >= 2:
                self.debug.log(
                    f"_is_real_grid: flat cells form >=2 disjoint components "
                    "→ reject (click-puzzle pattern)."
                )
                return False
            return True
        except Exception as e:
            self.debug.log(f"_is_real_grid check errored ({e}); accepting grid.")
            return True

    def _solve_grid(
        self,
        image_path: str,
        grid_boxes: List[Tuple[int, int, int, int]],
        retry_mode: Optional[str] = None,
    ) -> Union[ClickAction, DoneAction, WaitAction]:
        n = len(grid_boxes)
        if n == 9:
            rows, cols = 3, 3
        elif n == 16:
            rows, cols = 4, 4
        else:
            cols = int(math.sqrt(n))
            rows = math.ceil(n / cols)
        self.debug.log(f"grid {rows}x{cols} ({n} cells)")

        cv_selected: List[int] = []
        cv_loading: List[int] = []
        try:
            cv_selected, cv_loading = detect_selected_cells(image_path, grid_boxes, self.debug)
            if cv_selected:
                self.debug.log(f"CV: cells already selected -> {cv_selected}")
            if cv_loading:
                self.debug.log(f"CV: cells loading -> {cv_loading}")
        except Exception as e:
            self.debug.log(f"detect_selected_cells failed: {e}")

        # SINGLE canonical overlay: get_numbered_grid_overlay (RED labels,
        # top-right, ALL cells 1..N) — byte-for-byte the same overlay used to
        # generate the training data (scripts/build_grid_overlays.py) and the
        # offline grader. The model was trained ONLY on this style; any other
        # overlay (e.g. green, or skipping cells) is out-of-distribution and
        # tanks the live solve rate. We number EVERY cell so the model sees the
        # exact grid it trained on; already-selected/loading cells are filtered
        # AFTER the model responds (below), never by renumbering the grid.
        ext = os.path.splitext(image_path)[1] or ".png"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tf:
            overlay_path = tf.name
        self._temp_files.append(overlay_path)
        get_numbered_grid_overlay(image_path, grid_boxes, output_path=overlay_path)
        self.debug.save_image(overlay_path, "01_grid_overlay.png")

        with timed("planner.grid"):
            selected = self.planner.get_grid_selection(
                overlay_path, rows=rows, cols=cols, retry_mode=retry_mode,
            )

        # Map the model's tile IDs to clicks. Skip cells the CV layer already
        # flagged as selected (avoid re-toggling) or still loading.
        final: List[int] = []
        for n in selected:
            try:
                v = int(n)
            except (TypeError, ValueError):
                continue
            if v < 1 or v > len(grid_boxes):
                continue  # hallucinated index
            if v in cv_selected or v in cv_loading:
                continue  # already selected / not ready — don't re-click
            final.append(v)

        if not final:
            # Nothing new to click: wait if tiles are still loading, else done.
            if cv_loading:
                return WaitAction(action="wait", duration_ms=1000)
            return DoneAction(action="done")

        img_w, img_h = self._image_size  # type: ignore[misc]
        bboxes: List[List[float]] = []
        for v in final:
            x1, y1, x2, y2 = grid_boxes[v - 1]
            bboxes.append([x1 / img_w, y1 / img_h, x2 / img_w, y2 / img_h])
        return ClickAction(action="click", target_bounding_boxes=bboxes)


def solve_captcha(media_path: str, instruction: str = "", **kwargs) -> Any:
    return CaptchaSolver(**kwargs).solve(media_path, instruction)
