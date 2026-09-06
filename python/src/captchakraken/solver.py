"""
CaptchaSolver (v2) — vLLM-backed.

Flow:
  1. find_grid → if a grid is detected, draw the numbered overlay, ask the
     `captcha` LoRA which cells to click, return ClickActions with
     per-tile bounding boxes.
  2. find_checkbox on small images → if a lone checkbox is detected, return a
     ClickAction targeting it directly.
  3. Otherwise (click / drag / other still-image puzzle) → route the image to
     the full-puzzle pixel/action path (planner.get_pixel_actions), which the
     LoRA is trained on. Only raise UnsupportedCaptchaError if the model returns
     nothing usable.

ANIMATED challenges take a separate entry point, `solve_keyframes`. They used to be
detected and skipped: the settle monitor called them "never settles" and the solve
was abandoned. Now the driver records the widget, `keyframes.py` slices the
recording into the few stills that carry the answer, and those go to the model as
one multi-image request. The answer comes back with a `frame`, and the driver waits
for the page to look like that frame before it clicks — because on these puzzles
the coordinates are only correct while the target is actually on screen.

v1 had a SAM3-backed tool-using planner with detect/segment/drag-refine; it
lives on the `v1-old-architecture` branch.
"""

import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple, Union

from PIL import Image

from .action_types import (
    CaptchaAction,
    ClickAction,
    DoneAction,
    DragAction,
    TypeAction,
    WaitAction,
)
from .image_processor import ImageProcessor
from .planner import ActionPlanner
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


#: The only cell counts a registered grid puzzle has. `find_grid` is a lattice
#: detector, not a catalogue — it allows rectangles and any dimension from
#: MIN_GRID_DIM up — so it will happily report 12 or 20 cells on a click puzzle's
#: header/footer bands. Anything not in here is a false positive by construction,
#: and it is also a board the model has never seen: the training pipeline's own
#: `grid_overlay.detect_cells` refuses every count but these two.
GRID_CELL_SHAPES = {9: (3, 3), 16: (4, 4)}

#: Cell counts each vendor actually ships, for the vendors the driver can name.
#: Only reCAPTCHA has a 4x4; hCaptcha's single grid puzzle is 3x3, so 16 cells on
#: an hCaptcha board is a contradiction rather than a close call. Measured on real
#: captures, that one line removes four of the five non-grid boards that clear
#: both find_grid and _is_real_grid today.
#:
#: A vendor NOT in this table — `unknown`, or anything new — gets every shape.
#: `unknown` is what the driver reports for GeeTest and Prosopo, both of which
#: ship real 3x3 grids, and it is what the one-shot image API defaults to, which
#: is the path the offline grader uses to score reCAPTCHA 4x4. Narrowing it would
#: stop scoring a whole puzzle type while looking like a tightening.
VENDOR_GRID_CELLS = {
    "hcaptcha": frozenset({9}),
    "recaptcha": frozenset({9, 16}),
}


def _grid_dims(n_cells, puzzle_source="unknown"):
    """(rows, cols) if this detection could be a grid we ship, else None.

    None means "do not solve this as a grid" — the caller falls through to the
    click/drag path, which is what the board actually is. It is deliberately not
    an exception: a false positive here is an ordinary event, not an error.
    """
    shape = GRID_CELL_SHAPES.get(n_cells)
    if shape is None:
        return None
    allowed = VENDOR_GRID_CELLS.get(puzzle_source)
    if allowed is not None and n_cells not in allowed:
        return None
    return shape


class CaptchaSolver:
    """v2 solver: OpenCV grid detection + vLLM `captcha` LoRA."""

    def __init__(
        self,
        model: Optional[str] = None,
        provider: str = "captchaKrakenApi",
        api_key: Optional[str] = None,
        expert: Optional[str] = None,
    ):
        self.debug = DebugManager(DEBUG)
        # `provider` kept for argv compatibility with the v1 CLI signature.
        if provider not in {"captchaKrakenApi"}:
            self.debug.log(f"Provider {provider!r} ignored; v2 only supports captchaKrakenApi.")
        self.planner = ActionPlanner(
            model=model, api_key=api_key, debug_callback=self.debug.log,
            expert=expert,
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
        text_mode: bool = False,
    ) -> Union[CaptchaAction, List[CaptchaAction]]:
        media_path = str(Path(media_path).resolve())
        if not os.path.exists(media_path):
            raise FileNotFoundError(f"Media not found: {media_path}")

        cv_image_path = self._materialize_image(media_path)
        self.debug.save_image(cv_image_path, "00_base_image.png")
        assert self._image_size is not None
        img_w, img_h = self._image_size

        if text_mode:
            # The driver saw a text box in the widget, so the answer is a string
            # and there is nothing on the picture to find. Grid and checkbox
            # detection are skipped rather than merely ignored: BotDetect's
            # boxed, evenly-spaced glyphs are exactly the lattice find_grid
            # looks for, and a false grid here would answer a typing puzzle
            # with a list of cell numbers.
            try:
                actions = self._solve_pixel(cv_image_path, text_mode=True)
            except ValueError as exc:
                # The served model predates the distorted-text family, so there
                # is no prompt to send it. Surfaced as UNSUPPORTED rather than
                # crashing: it is the same class of outcome as "this LoRA only
                # does grids", the driver already knows how to report it, and
                # the message names the real fix (a generation-2 model) instead
                # of a stack trace in prompts.py.
                raise UnsupportedCaptchaError(str(exc)) from exc
            if actions:
                return actions
            raise UnsupportedCaptchaError("Could not read the text captcha")

        with timed("solver.find_grid"):
            grid_boxes = find_grid(cv_image_path)
        # The SHAPE gate runs before the content gate: a lattice no vendor ships
        # is not a grid whatever its cells look like, and saying so here keeps
        # `_solve_grid` from ever being handed dimensions it would have to invent.
        dims = _grid_dims(len(grid_boxes), puzzle_source) if grid_boxes else None
        if grid_boxes and dims and self._is_real_grid(cv_image_path, grid_boxes):
            self.debug.log(f"Detected grid with {len(grid_boxes)} cells")
            return self._solve_grid(cv_image_path, grid_boxes, dims[0], dims[1],
                                    retry_mode=retry_mode)
        elif grid_boxes and not dims:
            # e.g. a 16-cell lattice on an hCaptcha board (it ships no 4x4), or a
            # 12-cell one on anything. Fall through to the click path, which is
            # what this puzzle is.
            self.debug.log(
                f"find_grid returned {len(grid_boxes)} cells, which is not a shape "
                f"{puzzle_source} ships — not solving as a grid."
            )
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
        # LoRA is trained on all of these, so route the image to the pixel
        # action path rather than bailing. (Animated challenges come in through
        # `solve_keyframes` instead, so anything here is a still worth attempting.)
        actions = self._solve_pixel(cv_image_path)
        if actions:
            return actions

        # The model returned nothing usable (blank/unrendered frame or a truly
        # un-actionable image). Surface as unsupported so the caller fails fast
        # instead of clicking nothing.
        raise UnsupportedCaptchaError("Cannot solve this kind of captcha")

    def solve_keyframes(
        self, keyframe_paths: Sequence[str]
    ) -> List[Union[ClickAction, DragAction]]:
        """Solve an ANIMATED challenge from the keyframes of a recording.

        `keyframe_paths` must be in model order (frame 1 first) — the numbers the
        answer refers to are positional, so a reordered list silently remaps the
        answer onto the wrong picture. `keyframes.materialize_keyframes` and
        `read_keyframe_paths` both return them correctly ordered; do not sort them
        yourself with a plain string sort past nine frames.

        Grid detection is deliberately NOT attempted. An animated challenge is never
        a tile grid (vendors do not animate those), and `find_grid` false-positives
        on the header/footer bands of hCaptcha's click puzzles — which is what these
        are. Every returned action carries `await_keyframe`, the still the driver
        must see on screen before it acts.
        """
        paths = [str(p) for p in keyframe_paths]
        if not paths:
            raise UnsupportedCaptchaError("no keyframes to solve from")
        with Image.open(paths[0]) as im:
            self._image_size = im.size
        self.debug.save_image(paths[0], "00_keyframe_01.png")
        self.debug.log(f"solving animated challenge from {len(paths)} keyframes")

        actions = self._to_actions(
            self.planner.get_keyframe_actions(paths), keyframe_paths=paths
        )
        if actions:
            return actions
        raise UnsupportedCaptchaError("Cannot solve this animated captcha")

    def _solve_pixel(
        self, image_path: str, text_mode: bool = False
    ) -> List[Union[ClickAction, DragAction]]:
        """Turn the model's normalized 0–1 click/drag actions into ClickAction /
        DragAction bboxes. Each point becomes a small box centered on it (the TS
        solver clicks the box center)."""
        return self._to_actions(self.planner.get_pixel_actions(image_path, text_mode=text_mode))

    def _to_actions(
        self,
        raw_actions: List[dict],
        keyframe_paths: Optional[Sequence[str]] = None,
    ) -> List[Union[ClickAction, DragAction]]:
        """Shared conversion from the planner's normalized 0–1 actions to typed
        Click/Drag actions with small boxes around each point.

        When `keyframe_paths` is given, the chosen keyframe is attached so the
        driver knows which page state to wait for. A model answer with no usable
        frame is still converted — it just carries no keyframe, and the driver
        treats that as "act on the current frame". That is the honest fallback:
        refusing the action outright would turn a good coordinate into a failed
        solve over a missing integer.
        """
        R = _PIXEL_BOX_HALF
        clamp = lambda v: min(max(v, 0.0), 1.0)

        def box(cx: float, cy: float) -> List[float]:
            return [clamp(cx - R), clamp(cy - R), clamp(cx + R), clamp(cy + R)]

        def wait_for(a: dict) -> dict:
            if not keyframe_paths:
                return {}
            frame = a.get("frame")
            if not isinstance(frame, int) or not 1 <= frame <= len(keyframe_paths):
                self.debug.log(
                    "animated answer named no usable keyframe; acting on the "
                    "current frame without waiting"
                )
                return {}
            return {"frame": frame, "await_keyframe": str(keyframe_paths[frame - 1])}

        out: List[Union[ClickAction, DragAction]] = []
        for a in raw_actions:
            if a.get("kind") == "click":
                boxes = [box(x, y) for (x, y) in a.get("points", [])]
                if boxes:
                    out.append(
                        ClickAction(action="click", target_bounding_boxes=boxes,
                                    **wait_for(a))
                    )
            elif a.get("kind") == "drag":
                sx, sy = a["src"]
                dx, dy = a["dst"]
                out.append(
                    DragAction(
                        action="drag",
                        source_bounding_box=box(sx, sy),
                        target_bounding_box=box(dx, dy),
                        **wait_for(a),
                    )
                )
            elif a.get("kind") == "slide":
                # A puzzle-piece slider, carried as a drag with NO source. That
                # is not a lossy encoding of a normal drag — it is the whole
                # shape of the answer: the thing you grab (the handle) is not
                # the thing that has to arrive (the piece), and only the page
                # knows where the handle is. The driver reads a null source as
                # "find the slider and close the loop on the piece".
                dx, dy = a["dst"]
                out.append(
                    DragAction(
                        action="drag",
                        source_bounding_box=None,
                        target_bounding_box=box(dx, dy),
                        **wait_for(a),
                    )
                )
            elif a.get("kind") == "type":
                out.append(TypeAction(action="type", text=a["text"]))
        return out

    # Back-compat alias. Kept pointing at `solve` (a single still), NOT at
    # `solve_keyframes`: callers of this name pass one media path and expect one
    # answer, and quietly reinterpreting that as "record and slice" would change
    # what an old integration does. New code calls `solve_keyframes` explicitly.
    def solveVideo(self, *args, **kwargs):
        return self.solve(*args, **kwargs)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _materialize_image(self, media_path: str) -> str:
        """Return a path to a static PNG (extracting first frame for videos)."""
        is_video = any(media_path.lower().endswith(ext) for ext in [".mp4", ".gif", ".avi", ".webm"])
        if is_video:
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

            self.debug.log(
                f"_is_real_grid: per-cell stddev = {[round(s, 1) for s in stds]}"
            )

            # A SPRITE BOARD is not a tile grid, however grid-shaped it is.
            #
            # GeeTest's iconcrush and gobang are lattices of sprites drawn on
            # ONE flat board, so find_grid finds them and every cell is
            # colourful enough (mean stddev ~52) to clear every check below.
            # iconcrush was routed to `_solve_grid` on every capture, which
            # asks the model to "choose the cell numbers that match the
            # description" — the wrong question for a board you answer by
            # tapping two tiles to swap them. Nothing errors: the reply comes
            # back as `[3, 6, 9]`, is re-encoded as clicks on those cell
            # centres, and is graded against a two-click gold. The type scored
            # 0.089 that way and read as the model's weakest.
            #
            # The discriminator is the BACKGROUND, not the content. A real
            # captcha grid is independent photographs, so each tile's median
            # colour is its own; a sprite board shares one board colour behind
            # every cell, so the medians collapse onto a single value.
            # Measured over the real captures: iconcrush and gobang 0.0-0.8,
            # against 14.9 (recaptcha_grid_4x4) up to 93.0 (geetest_v4_nine)
            # for the five true grid families. The threshold sits in an 18x
            # gap, which is why it is a fixed number rather than a tuned one.
            medians = []
            for (x1, y1, x2, y2) in grid_boxes:
                roi = img[y1:y2, x1:x2]
                if roi.size:
                    medians.append(np.median(roi.reshape(-1, 3), axis=0))
            if len(medians) >= 4:
                spread = float(np.mean(np.std(np.array(medians), axis=0)))
                self.debug.log(f"_is_real_grid: median-colour spread = {spread:.1f}")
                if spread < 6.0:
                    self.debug.log(
                        f"_is_real_grid: every cell shares one background colour "
                        f"({spread:.1f} < 6.0) → reject (sprite board, not tiles)."
                    )
                    return False

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

            # THE FLAT-CELL CONTIGUITY HEURISTIC IS GONE, and it was the only
            # thing rejecting real grids.
            #
            # It assumed flat tiles in a genuine photo grid always form ONE
            # contiguous blob (a top-left sky stack, a bottom road row), because
            # the board is a single photo cut up; a click-puzzle false grid puts
            # its flat cells in the left and right margins of the middle row,
            # which is two components. True of the boards it was written for,
            # and false of plenty of photographs — a subject centred against
            # plain sky leaves two flat corners and nothing between them.
            #
            # MEASURED over every real capture we hold, restricted to lattices
            # `_grid_dims` actually accepts (9 or 16 cells — a 25-cell gobang
            # board never reaches this function as a grid decision):
            #
            #     rule                     grid false neg   board false pos
            #     spread + mean-stddev        2/1748 0.11%       0/269 0.00%
            #     ...plus contiguity         40/1748 2.29%       0/269 0.00%
            #
            # It cost 38 real grids and caught nothing the two checks above did
            # not already catch — 19 of those were recaptcha_grid_4x4 alone,
            # 5.5% of that family, every one of them sent to the click path to
            # be asked for coordinates on a board answered with cell numbers.
            # Nothing errors when that happens; the reply is simply graded
            # against a gold it cannot match.
            #
            # The median-colour spread check above supersedes it and is a much
            # cleaner separation (an 18x gap rather than a topological guess),
            # which is why this is a deletion and not a retuning.
            return True
        except Exception as e:
            self.debug.log(f"_is_real_grid check errored ({e}); accepting grid.")
            return True

    def _solve_grid(
        self,
        image_path: str,
        grid_boxes: List[Tuple[int, int, int, int]],
        rows: int,
        cols: int,
        retry_mode: Optional[str] = None,
    ) -> Union[ClickAction, DoneAction, WaitAction]:
        # The shape is PASSED IN, already cleared by `_grid_dims`. It used to be
        # derived here, and the derivation had an `else` branch that turned any
        # unrecognised count into `cols = int(sqrt(n)); rows = ceil(n / cols)` —
        # so a 12-cell false positive became a 4x3 board, got stamped with twelve
        # numbers, and was answered with cell ids. No such board can exist in the
        # training corpus, because the generator's own detector refuses every
        # count but 9 and 16.
        self.debug.log(f"grid {rows}x{cols} ({len(grid_boxes)} cells)")

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


def solve_captcha(
    media_path: str,
    instruction: str = "",
    *,
    puzzle_source: str = "unknown",
    retry_mode: Optional[str] = None,
    text_mode: bool = False,
    **kwargs: Any,
) -> Any:
    """Solve one captcha image. `kwargs` configure the solver; the arguments
    named above are forwarded to `CaptchaSolver.solve`.

    They are spelled out rather than left in `**kwargs` because everything in
    `kwargs` goes to the CONSTRUCTOR. A caller passing `text_mode=True` — the
    only way this one-shot API can say "the answer is a typed string, not a
    place on the picture" — got `TypeError: __init__() got an unexpected keyword
    argument`, so a static distorted-text image was unsolvable through the
    public entry point. Only the `PageSolver` path could reach the text prompt,
    and it decides `text_mode` from the DOM, which a bare image does not have.
    """
    return CaptchaSolver(**kwargs).solve(
        media_path, instruction,
        puzzle_source=puzzle_source, retry_mode=retry_mode, text_mode=text_mode,
    )
