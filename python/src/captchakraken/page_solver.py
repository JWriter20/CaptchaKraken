"""Browser page driver: the Python mirror of js/src/solver.ts. Sync Playwright only."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import planner
from .action_types import CaptchaAction
from .humanize import Humanizer, resolve as resolve_humanizer
from .solver import CaptchaSolver, UnsupportedCaptchaError
from .timing import PhaseBudget, timings_enabled

DEBUG = os.getenv("CAPTCHA_DEBUG", "0") == "1"
_SESSION_ENV = "CAPTCHA_KRAKEN_SESSION"


def _log(message: str) -> None:
    print(f"[captchakraken] {message}", flush=True)


def _debug(message: str) -> None:
    if DEBUG:
        print(f"[captchakraken:debug] {message}", flush=True)


def _delay(ms: float) -> None:
    if ms > 0:
        time.sleep(ms / 1000.0)


def _now() -> float:
    return time.monotonic() * 1000.0


def _tmp_png(prefix: str) -> str:
    fd, path = tempfile.mkstemp(prefix=f"{prefix}_", suffix=".png")
    os.close(fd)
    return path


def _unlink(path: Optional[str]) -> None:
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except OSError:
            pass


def _sha1(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()


class CaptchaSolveError(Exception):
    pass


class AnimatedChallengeError(CaptchaSolveError):
    pass


class UnsupportedChallengeError(CaptchaSolveError):
    pass


class NoCaptchaFoundError(CaptchaSolveError):
    pass


class PageClosedError(CaptchaSolveError):
    pass


_STALE_HANDLE_RE = re.compile(r"Timeout .*exceeded|not visible|not attached|detached", re.IGNORECASE)
_CLOSED_TARGET_RE = re.compile(
    r"Target (?:page, context or browser )?(?:has been )?closed|Session closed", re.IGNORECASE)


def answer_needs_element_box(actions: List[Dict[str, Any]]) -> bool:
    return any((a or {}).get("action") != "done" for a in actions)


@dataclass
class SolveResult:
    is_solved: bool
    final_mouse_position: Tuple[float, float]
    token_usage: List[Dict[str, Any]] = field(default_factory=list)
    phases: Dict[str, float] = field(default_factory=dict)


@dataclass
class _GridSession:
    grid_boxes: List[Sequence[int]]
    element_box: Dict[str, float]
    scale_x: float
    scale_y: float
    screenshot_w: int
    screenshot_h: int


def settle_verdict(samples, *, settle_frames: int, animated_after_ms: int,
                   motion_streak: int = 0) -> str:
    """The pixel-settle rule over `(elapsed_ms, moved)` polls."""
    still = moved = 0
    for elapsed_ms, did_move in samples:
        if did_move:
            still, moved = 0, moved + 1
            if elapsed_ms >= animated_after_ms or (motion_streak and moved >= motion_streak):
                return "animated"
        else:
            moved, still = 0, still + 1
            if still >= settle_frames:
                return "settled"
    return "timeout"


def burst_hang_deadline_ms(cfg) -> float:
    return 3.0 * float(cfg.video_burst_max_ms) + 5_000.0


def measured_fps(frames: int, elapsed_ms: float, nominal_fps: float) -> float:
    if frames <= 0 or elapsed_ms <= 0:
        return float(nominal_fps)
    return frames / (elapsed_ms / 1000.0)


# Frame-diff thresholds; two screens of one board differ by ~0.0056, a different board by ~0.77.
_MOVED_DURING_INFERENCE_DIFF = 0.002
_NOT_THIS_BOARD_DIFF = 0.5
_NOT_THIS_BOARD_POLLS = 3


@dataclass
class PageSolverConfig:
    humanization: Optional[str] = None
    humanizer: Optional[Humanizer] = None
    touch_driver: Optional[Any] = None
    touch_transform: Optional[Dict[str, Any]] = None
    starting_mouse_position: Optional[Tuple[float, float]] = None
    expert: Optional[str] = None
    max_solve_loops: int = 6
    overall_solve_timeout_ms: int = 45_000
    max_no_progress_rounds: int = 2
    post_solve_delay_ms: int = 1_200
    post_solve_outcome_timeout_ms: int = 1_000
    post_solve_outcome_poll_ms: int = 75
    element_screenshot_timeout_ms: int = 8_000
    max_unsupported_resolves: int = 3
    max_stale_element_retries: int = 3
    stale_element_backoff_ms: int = 900
    stale_frame_resolve_enabled: bool = True
    stale_frame_diff_threshold: float = 0.02
    max_stale_frame_resolves: int = 2
    settle_poll_ms: int = 220
    settle_frames: int = 2
    settle_timeout_ms: int = 9_000
    animated_challenge_after_ms: int = 4_500
    animated_motion_streak: int = 5
    settle_diff_threshold: float = 0.01
    post_submit_change_timeout_ms: int = 4_000
    video_solve_enabled: bool = True
    animated_probe_enabled: bool = True
    video_burst_duration_ms: int = 4_000
    video_burst_max_ms: int = 12_000
    speculative_burst_enabled: bool = True
    video_burst_fps: int = 10
    keyframe_wait_timeout_ms: int = 9_000
    keyframe_wait_poll_ms: int = 120
    video_extra_inference_ms: int = 8_000
    hcaptcha_images_timeout_ms: int = 3_000
    grid_load_poll_interval_ms: int = 250
    grid_load_timeout_ms: int = 8_000
    board_paint_poll_ms: int = 180
    board_paint_timeout_ms: int = 2_500
    board_paint_floor: Optional[float] = None
    recaptcha_max_dynamic_rounds: int = 8
    recaptcha_fade_onset_grace_ms: int = 4_000
    recaptcha_dynamic_fade_poll_ms: int = 250
    recaptcha_dynamic_fade_wait_ms: int = 6_000
    recaptcha_tile_hover_enabled: bool = True
    slide_tolerance_px: float = 2.0
    slide_max_corrections: int = 3

    def video_budget_ms(self) -> int:
        return (self.video_burst_max_ms + self.keyframe_wait_timeout_ms
                + self.video_extra_inference_ms)


def vendor_from_src(src: Optional[str]) -> str:
    src = src or ""
    if "hcaptcha" in src:
        return "hcaptcha"
    if "recaptcha/api2" in src:
        return "recaptcha"
    return "unknown"


# Mirrored in solver.ts; keep both lists in the same order.
VENDOR_WIDGET_LOCATORS = [
    {"puzzle_source": "geetest", "selectors": [".geetest_box", ".geetest_panel_box", ".geetest_popup_window", ".geetest_widget"]},
    {"puzzle_source": "tencent", "selectors": ['#tcaptcha_transform_dy', '#tCaptchaDyContent', '.tencent-captcha-dy__content', 'iframe#tcaptcha_iframe_dy', 'iframe[id^="tcaptcha"]', 'iframe[src*="captcha.gtimg.com"]', 'iframe[src*="captcha.qq.com"]']},
    {"puzzle_source": "yidun", "selectors": [".yidun_panel", ".yidun"]},
    {"puzzle_source": "yandex", "selectors": ['iframe[src*="smartcaptcha.yandexcloud.net/advanced"]', 'iframe[src*="smartcaptcha.yandexcloud.net"]', ".CheckboxCaptcha"]},
    {"puzzle_source": "lemin", "selectors": ["#lemin-cropped-captcha", ".lemin-captcha-popup"]},
    {"puzzle_source": "prosopo", "selectors": [".prosopo-modalInner", ".procaptcha-checkbox"]},
    {"puzzle_source": "mtcaptcha", "selectors": ['iframe[src*="service.mtcaptcha.com"]', 'iframe[id^="mtcaptcha-iframe"]', ".mtcaptcha", ".mtcap"]},
    {"puzzle_source": "botdetect", "selectors": [".BDC_CaptchaDiv"]},
]

VENDORS_WITH_BESPOKE_HANDLING = frozenset({"hcaptcha", "recaptcha"})

# BotDetect is self-hosted and has no vendor host, so it is deliberately absent.
VENDOR_URL_MARKERS = [
    {"puzzle_source": "hcaptcha", "hosts": ["hcaptcha.com"]},
    {"puzzle_source": "recaptcha", "hosts": ["google.com/recaptcha", "recaptcha.net"]},
    {"puzzle_source": "turnstile", "hosts": ["challenges.cloudflare.com"]},
    {"puzzle_source": "geetest", "hosts": ["geetest.com"]},
    {"puzzle_source": "tencent", "hosts": ["captcha.gtimg.com", "captcha.qcloud.com"]},
    {"puzzle_source": "yidun", "hosts": ["dun.163.com", "cstaticdun.126.net", "necaptcha.nosdn.127.net"]},
    {"puzzle_source": "yandex", "hosts": ["smartcaptcha.yandexcloud.net"]},
    {"puzzle_source": "lemin", "hosts": ["leminnow.com"]},
    {"puzzle_source": "prosopo", "hosts": ["prosopo.io"]},
    {"puzzle_source": "mtcaptcha", "hosts": ["mtcaptcha.com"]},
]

# Vendor-named first, generic last: the driver takes the first visible match.
TEXT_INPUT_VENDOR_SELECTORS = [
    "input[id*=captchaCode]", "input#captchaCode", "input[id*=validateCaptcha]",
    ".BDC_CaptchaDiv input[type=text]",
    "input.mtcap-inputtext", ".mtcap input[type=text]",
    ".AdvancedCaptcha-Input input", "input.Textinput-Control", 'input[name="rep"]',
]
TEXT_INPUT_GENERIC_SELECTORS = [
    'input[name*="captcha" i]', 'input[id*="captcha" i]', 'input[aria-label*="captcha" i]',
    'input[placeholder*="code" i]', 'input[autocomplete="off"][type=text]',
    "input[type=text]", "input:not([type])", "input[type=tel]", "textarea",
]
TEXT_INPUT_SELECTORS = TEXT_INPUT_VENDOR_SELECTORS + TEXT_INPUT_GENERIC_SELECTORS

SLIDER_HANDLE_SELECTORS = [
    ".geetest_slider_button", ".geetest_btn", ".geetest_slider .geetest_arrow",
    ".tencent-captcha-dy__slider-block", "#tcaptcha_drag_thumb", ".tc-slider-normal", "[id*=slideBlock]",
    ".yidun_slider", ".yidun_jigsaw",
    ".lemin-slider-handle", "#lemin-cropped-captcha .slider",
    '[role="slider"]', "[aria-valuenow]",
    '[class*="slider"][class*="btn"]', '[class*="slider"][class*="button"]',
    '[class*="slide"][class*="handle"]', '[class*="drag"][class*="thumb"]',
]
DRAGGABLE_PIECE_SELECTORS = [
    ".lemin-cropped-puzzle-piece", "#lemin-cropped-captcha canvas + canvas",
    '[class*="puzzle"][class*="piece"]', '[class*="jigsaw"]',
]
SLIDE_PIECE_MEASURE_SELECTORS = (
    ".geetest_slice", ".tencent-captcha-dy__fg-item", ".yidun_jigsaw", ".lemin-cropped-puzzle-piece",
    '[class*="puzzle"][class*="piece"]', '[class*="jigsaw"]',
)
SLIDE_PIECE_MIN_PX = 3.0
SLIDE_PIECE_MAX_FRACTION = 0.6

_GEETEST_ACCEPTED_SELECTOR = (
    ".geetest_result_tips.geetest_success, .geetest_captcha.geetest_success, "
    ".geetest_captcha.geetest_lock_success")
_RECAPTCHA_BANNERS = (
    (".rc-imageselect-error-select-more", "select-more"),
    (".rc-imageselect-error-dynamic-more", "dynamic-more"),
    (".rc-imageselect-incorrect-response", "rejected"),
)
_HCAPTCHA_IMAGES_READY_JS = """() => {
    const vis = (el) => !!el && el.getClientRects().length > 0
        && getComputedStyle(el).visibility !== 'hidden';
    const prompt = document.querySelector('.prompt-text');
    if (prompt && !vis(prompt)) return false;
    const tiles = Array.from(document.querySelectorAll('.task-image .image, .task .image'));
    if (tiles.length > 0) {
        return tiles.every((el) => {
            const bg = getComputedStyle(el).backgroundImage;
            return bg && bg !== 'none' && !/url\\(["']?["']?\\)/.test(bg);
        });
    }
    const canvas = document.querySelector('canvas');
    if (canvas && canvas.width > 0 && canvas.height > 0) return true;
    const example = document.querySelector('.challenge-example img, .image-wrapper img');
    if (example) return example.complete && example.naturalWidth > 0;
    return true;
}"""
_RESOURCES_JS = """() => {
  const out = [];
  try { for (const e of performance.getEntriesByType('resource')) out.push(e.name); } catch (err) {}
  for (const el of document.querySelectorAll('script[src],iframe[src],link[href],img[src]')) {
    out.push(el.getAttribute('src') || el.getAttribute('href') || '');
  }
  return out;
}"""


class PageSolver:
    """Drives one Playwright-compatible `page` through a captcha. Reusable across solves."""

    def __init__(self, config: Optional[PageSolverConfig] = None,
                 solver: Optional[CaptchaSolver] = None, **solver_kwargs: Any) -> None:
        self.config = config or PageSolverConfig()
        if self.config.expert is not None:
            solver_kwargs.setdefault("expert", self.config.expert)
        self._solver = solver or CaptchaSolver(**solver_kwargs)
        self._human: Humanizer = resolve_humanizer(self.config)
        self._last_submit_frame_hash: Optional[str] = None
        self._deadline_ms: Optional[float] = None
        self._budget: Optional[PhaseBudget] = None
        self._animated_plan: Optional[Tuple[List[str], str, Any, Any]] = None
        self._reset_animated_state()

    @property
    def _last_mouse(self) -> Tuple[float, float]:
        return self._human.at

    @_last_mouse.setter
    def _last_mouse(self, at: Tuple[float, float]) -> None:
        self._human.at = (float(at[0]), float(at[1]))

    @contextmanager
    def _phase(self, name: str):
        if self._budget is None:
            yield
            return
        with self._budget.phase(name):
            yield

    # ── per-solve state ──────────────────────────────────────────────────

    def _reset_animated_state(self) -> None:
        self._acted_on_board = False
        self._known_animated = False
        self._animated_probe_armed = False
        self._animated_probe_done = False
        self._video_budget_granted = False
        self._discard_animated_plan()
        self._keyframe_mode: Optional[str] = None
        self._keyframe_steady_screens = 0
        self._last_answer_sig: Optional[str] = None
        self._no_progress_rounds = 0
        self._resample_level = 0
        self._apply_sampling()

    @staticmethod
    def _answer_signature(actions: Sequence[Any], retry_mode: Optional[str]) -> Optional[str]:
        try:
            parts = [(a.get("action"), _round_pts(a.get("target_bounding_boxes")),
                      _round_pts(a.get("target_bounding_box")), _round_pts(a.get("target_coordinates")),
                      _round_pts(a.get("source_bounding_box")), a.get("text"))
                     for a in map(_as_dict, actions)]
            return repr((retry_mode, parts))
        except Exception:
            return None

    def _note_answer(self, actions: Sequence[Any], retry_mode: Optional[str]) -> None:
        """A repeated answer already ran and changed nothing: resample, re-ask, and arm the probe."""
        sig = self._answer_signature(actions, retry_mode)
        if sig is not None and sig == self._last_answer_sig:
            self._no_progress_rounds += 1
            _log(f"[no-progress] the model returned the same answer again "
                 f"({self._no_progress_rounds}/{self.config.max_no_progress_rounds})")
            self._resample_level += 1
            self._apply_sampling()
            self._invalidate_animated_answer()
            self._arm_animated_probe()
        else:
            self._no_progress_rounds = 0
            self._last_answer_sig = sig

    def _apply_sampling(self) -> None:
        target = getattr(self._solver, "planner", None)
        if target is None:
            return
        target.sampling = sampling = planner.sampling_for_level(self._resample_level)
        if sampling:
            _log(f"[resample] temperature {sampling['temperature']}, seed {sampling['seed']}")

    def _fresh_board(self) -> None:
        self._resample_level = 0
        self._acted_on_board = False
        self._apply_sampling()

    def _check_deadline(self, where: str) -> None:
        if self._deadline_ms is not None and _now() > self._deadline_ms:
            raise CaptchaSolveError(
                f"captcha solve exceeded overall_solve_timeout_ms "
                f"({self.config.overall_solve_timeout_ms}ms) during {where}")

    def _grant_video_budget(self) -> None:
        """The recording path buys its own budget once per solve."""
        cfg = self.config
        if cfg.video_solve_enabled and not self._video_budget_granted:
            self._video_budget_granted = True
            if self._deadline_ms is not None:
                self._deadline_ms += cfg.video_budget_ms()
                _log(f"[animated] +{cfg.video_budget_ms()}ms for the recording path")

    # ── the shared half, in-process ──────────────────────────────────────

    def _find_grid(self, image_path: str) -> Optional[List[Sequence[int]]]:
        from .tool_calls.find_grid import find_grid

        try:
            return find_grid(image_path)
        except Exception as exc:
            _debug(f"find_grid failed: {exc}")
            return None

    def _has_movement(self, path_a: str, path_b: str, threshold: float) -> bool:
        from .image_processor import ImageProcessor

        try:
            return bool(ImageProcessor.detect_movement(path_a, path_b, threshold))
        except Exception as exc:
            _debug(f"detect_movement failed: {exc}")
            return False

    def _grid_cell_states(self, path_a: str, path_b: str,
                          grid_boxes: Sequence[Sequence[int]]) -> Optional[Dict[str, List[int]]]:
        from .cli import grid_cell_states

        try:
            return grid_cell_states(path_a, path_b, list(grid_boxes))
        except Exception as exc:
            _debug(f"grid_cell_states failed: {exc}")
            return None

    def _board_painted(self, path: str) -> Optional[bool]:
        from .tool_calls.board_painted import board_is_painted

        try:
            return board_is_painted(path, self.config.board_paint_floor)["painted"]
        except Exception:
            return None

    def _ask(self, query: Callable[[], Any]) -> Tuple[List[CaptchaAction], List[Dict[str, Any]]]:
        """One model query; usage is read off the shared planner by delta."""
        p = self._solver.planner
        before = len(p.token_usage)
        actions = query()
        usage = [dict(u) for u in p.token_usage[before:]]
        return (actions if isinstance(actions, list) else [actions]), usage

    def _get_solution(self, image_path: str, puzzle_source: str, retry_mode: Optional[str],
                      text_mode: bool = False):
        return self._ask(lambda: self._solver.solve(
            image_path, puzzle_source=puzzle_source, retry_mode=retry_mode, text_mode=text_mode))

    def _get_keyframe_solution(self, keyframe_paths: Sequence[str]):
        return self._ask(lambda: self._solver.solve_keyframes(keyframe_paths))

    # ── gestures ─────────────────────────────────────────────────────────

    def _smooth_move(self, page: Any, x: float, y: float) -> None:
        with self._phase("mouse"):
            self._human.move(page, (x, y))

    def _move_to_element(self, page: Any, element: Any, padding_percentage: float = 25.0) -> None:
        # Bounded: Playwright's default 30s stability wait hangs on an animating frame.
        try:
            element.scroll_into_view_if_needed(timeout=2_000)
        except TypeError:
            try:
                element.scroll_into_view_if_needed()
            except Exception:
                pass
        except Exception:
            pass
        box = element.bounding_box()
        if not box:
            raise CaptchaSolveError("element has no bounding box")
        pad = padding_percentage / 100.0
        self._smooth_move(
            page,
            box["x"] + box["width"] * (pad + random.random() * (1 - 2 * pad)),
            box["y"] + box["height"] * (pad + random.random() * (1 - 2 * pad)))

    def _move_and_click(self, page: Any, element: Any) -> None:
        self._move_to_element(page, element)
        with self._phase("mouse"):
            self._human.click(page, self._last_mouse)

    @staticmethod
    def _click_point_for(action: Dict[str, Any],
                         element_box: Dict[str, float]) -> Optional[Tuple[float, float]]:
        """Element-relative click point: a random spot inside the box, inset 10% off its border."""
        bbox = action.get("target_bounding_box")
        coords = action.get("target_coordinates")
        if bbox:
            x1, y1, x2, y2 = (float(v) for v in bbox)
            w, h = element_box["width"], element_box["height"]
            px, py = (x2 - x1) * w * 0.1, (y2 - y1) * h * 0.1
            return (x1 * w + px + random.random() * ((x2 - x1) * w - 2 * px),
                    y1 * h + py + random.random() * ((y2 - y1) * h - 2 * py))
        if coords:
            return float(coords[0]) * element_box["width"], float(coords[1]) * element_box["height"]
        return None

    def _execute_click(self, page: Any, action: Dict[str, Any], element_box: Dict[str, float],
                       element: Any = None, await_keyframe: Optional[str] = None) -> None:
        self._acted_on_board = True
        rel = self._click_point_for(action, element_box)
        if rel is None:
            _log("click action without coordinates or bounding box; skipping")
            return
        at = (element_box["x"] + rel[0], element_box["y"] + rel[1])
        if await_keyframe:
            # Park on the target first, so only a mouse-down separates the right screen from the click.
            with self._phase("mouse"):
                self._human.move(page, at)
            self._wait_for_keyframe(element, await_keyframe,
                                    (rel[0] / element_box["width"], rel[1] / element_box["height"]))
        with self._phase("mouse"):
            self._human.click(page, at)

    def _click_when_frame_matches(self, page: Any, element: Any, action: Dict[str, Any],
                                  element_box: Dict[str, float], await_keyframe: str) -> None:
        self._execute_click(page, action, element_box, element, await_keyframe)

    def _execute_drag(self, page: Any, action: Dict[str, Any], element_box: Dict[str, float]) -> None:
        self._acted_on_board = True

        def center(bbox):
            return (element_box["x"] + (float(bbox[0]) + float(bbox[2])) / 2 * element_box["width"],
                    element_box["y"] + (float(bbox[1]) + float(bbox[3])) / 2 * element_box["height"])

        with self._phase("mouse"):
            self._human.drag(page, center(action["source_bounding_box"]), center(action["target_bounding_box"]))

    def _find_control(self, scope: Any, selectors: Sequence[str]) -> Optional[Any]:
        for selector in selectors:
            try:
                element = scope.query_selector(selector)
            except Exception:
                continue
            if self._visible(element):
                return element
        return None

    def _measure_piece_box(self, scope: Any, widget_width: float) -> Optional[Dict[str, float]]:
        """The slider piece's box: the first visible match small enough to be a piece."""
        for selector in SLIDE_PIECE_MEASURE_SELECTORS:
            try:
                found = scope.query_selector_all(selector)
            except Exception:
                continue
            for candidate in found or ():
                try:
                    b = self._visible(candidate) and candidate.bounding_box()
                except Exception:
                    continue
                if b and SLIDE_PIECE_MIN_PX <= b["width"] <= widget_width * SLIDE_PIECE_MAX_FRACTION:
                    return b
        return None

    def _answer_box(self, scope: Any, element: Any = None) -> Optional[Any]:
        """The text box inside the widget, else a vendor-named one in its enclosing fieldset/form."""
        inside = self._find_control(scope, TEXT_INPUT_SELECTORS)
        if inside is not None or element is None:
            return inside
        for axis in ("ancestor::fieldset[1]", "ancestor::form[1]"):
            try:
                host = element.query_selector(f"xpath={axis}")
            except Exception:
                continue
            found = host and self._find_control(host, TEXT_INPUT_VENDOR_SELECTORS)
            if found:
                return found
        return None

    def _execute_type(self, page: Any, scope: Any, action: Dict[str, Any], element: Any = None) -> bool:
        self._acted_on_board = True
        text = str(action.get("text") or "")
        field_el = text and self._answer_box(scope, element)
        if not field_el:
            _log("type action, but no text box in the widget; skipping")
            return False
        self._move_and_click(page, field_el)
        if not self._human.type_text(page, field_el, text):
            return False
        _log(f"typed {len(text)} character(s) into the captcha field")
        return True

    def _track_piece(self, element: Any, before: str, after: str, exclude: Sequence[float],
                     travel: float = 0.0) -> Optional[Dict[str, Any]]:
        from .tool_calls.track_piece import changed_bbox, locate_piece

        try:
            self._screenshot(element, after, timeout_ms=self.config.element_screenshot_timeout_ms)
            bbox = changed_bbox(before, after, exclude)
            if bbox is None:
                return None
            return {"bbox": bbox, "piece": locate_piece(before, after, travel, exclude)}
        except Exception as exc:
            _debug(f"track_piece failed: {exc}")
            return None

    @staticmethod
    def _shot_scale(shot: str, css_width: float) -> float:
        dims = _read_png_dimensions(shot)
        return dims[0] / css_width if dims and css_width > 0 else 1.0

    def _execute_slide(self, page: Any, element: Any, scope: Any, action: Dict[str, Any],
                       element_box: Dict[str, float]) -> bool:
        """Aim the handle at the slot once, then look and correct until the piece is home."""
        self._acted_on_board = True
        tb = action["target_bounding_box"]
        target_x = (float(tb[0]) + float(tb[2])) / 2 * element_box["width"]

        handle = self._find_control(scope, SLIDER_HANDLE_SELECTORS)
        if handle is None:
            piece = self._find_control(scope, DRAGGABLE_PIECE_SELECTORS)
            box = piece and piece.bounding_box()
            if not box:
                _log("slide action, but the widget has neither a slider nor a draggable piece")
                return False
            target_y = (float(tb[1]) + float(tb[3])) / 2 * element_box["height"]
            with self._phase("mouse"):
                self._human.drag(page, (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2),
                                 (element_box["x"] + target_x, element_box["y"] + target_y))
            return True

        hbox = handle.bounding_box()
        if not hbox:
            return False
        start_x, hold_y = hbox["x"] + hbox["width"] / 2, hbox["y"] + hbox["height"] / 2
        # Mask from the handle's band to the widget's bottom: the filled track moves too.
        pad = max(4.0, hbox["height"] * 0.35)
        band = [0.0, hbox["y"] - element_box["y"] - pad, element_box["width"], element_box["height"]]
        shots = [_tmp_png("slide") for _ in range(2)]
        try:
            self._move_to_element(page, handle, padding_percentage=30.0)
            self._human.press(page)
            self._human.pause("grab")
            self._screenshot(element, shots[0], timeout_ms=self.config.element_screenshot_timeout_ms)
            scale = self._shot_scale(shots[0], element_box["width"])
            exclude = [v * scale for v in band]

            def live_center():
                b = self._measure_piece_box(scope, element_box["width"])
                return None if b is None else b["x"] + b["width"] / 2.0 - element_box["x"]

            rest = live_center()
            offset = target_x - (start_x - element_box["x"] if rest is None else rest)
            self._smooth_move(page, start_x + offset, hold_y)

            widths: List[Tuple[float, float]] = []
            last_box = last_piece = None
            ratio, steered = 1.0, False
            for _ in range(self.config.slide_max_corrections):
                self._human.pause("probe")
                seen = self._track_piece(element, shots[0], shots[1], exclude, travel=offset * ratio * scale)
                if seen is not None:
                    box = seen["bbox"]
                    widths.append((offset, float(box[2] - box[0]) / scale))
                    last_box, last_piece = box, seen["piece"]
                piece_w, ratio = self._solve_slide_geometry(widths, element_box["width"])
                live = live_center()
                if live is not None:
                    center = live
                elif last_piece is not None:
                    center = last_piece["centre"] / scale
                elif last_box is not None and piece_w is not None:
                    center = last_box[2] / scale - piece_w / 2.0
                else:
                    continue
                steered = True
                error = target_x - center
                _debug(f"slider: center={center:.1f} target={target_x:.1f} error={error:+.1f} ratio={ratio:.3f}")
                if abs(error) <= self.config.slide_tolerance_px:
                    break
                offset += error / ratio
                self._smooth_move(page, start_x + offset, hold_y)
            if not steered:
                _log("slider: the piece never resolved on screen; released where the opening sweep put it")
            self._human.pause("settle")
        finally:
            try:
                self._human.release(page)
            except Exception:
                pass
            for shot in shots:
                _unlink(shot)
        return True

    @staticmethod
    def _solve_slide_geometry(widths: Sequence[Tuple[float, float]],
                              widget_width: float) -> Tuple[Optional[float], float]:
        """Piece width and handle-to-piece ratio from `(offset, changed width)` readings."""
        if not widths:
            return None, 1.0
        piece_w, ratio = None, 1.0
        (o1, w1), (o2, w2) = min(widths, key=lambda m: m[0]), max(widths, key=lambda m: m[0])
        if o2 - o1 >= 16.0 and 0.2 <= (w2 - w1) / (o2 - o1) <= 3.0:
            ratio = (w2 - w1) / (o2 - o1)
            piece_w = w1 - ratio * o1
        if piece_w is None:
            o, w = widths[-1]
            piece_w = w - ratio * o
        if not (SLIDE_PIECE_MIN_PX <= piece_w <= widget_width * SLIDE_PIECE_MAX_FRACTION):
            return None, ratio
        return piece_w, ratio

    # ── detection ────────────────────────────────────────────────────────

    @staticmethod
    def _visible(element: Any) -> bool:
        try:
            return bool(element and element.is_visible())
        except Exception:
            return False

    def _has_non_empty_field_value(self, page: Any, selector: str) -> bool:
        try:
            element = page.query_selector(selector)
            if not element:
                return False
            value = element.get_attribute("value")
            if value is None:
                value = page.eval_on_selector(
                    selector, "node => (typeof node.value === 'string' ? node.value : '')")
            return bool(value and str(value).strip())
        except Exception:
            return False

    def _anchor_checked(self, anchor_iframe: Any, selector: str) -> bool:
        try:
            frame = anchor_iframe.content_frame()
            return bool(frame) and self._visible(frame.query_selector(selector))
        except Exception:
            return False

    def _is_recaptcha_anchor_checked(self, anchor_iframe: Any) -> bool:
        return self._anchor_checked(anchor_iframe, ".recaptcha-checkbox-checked")

    def _is_hcaptcha_anchor_checked(self, anchor_iframe: Any) -> bool:
        return self._anchor_checked(anchor_iframe, '#checkbox[aria-checked="true"]')

    def has_interactive_widget_in_dom(self, page: Any) -> bool:
        """Is a widget in the DOM at all, rendered or not? Invisible reCAPTCHA is excluded."""
        try:
            for anchor in page.query_selector_all('iframe[src*="recaptcha/api2/anchor"]'):
                if not re.search(r"[?&]size=invisible", anchor.get_attribute("src") or ""):
                    return True
            for sel in ('iframe[src*="recaptcha/api2/bframe"]',
                        'iframe[src*="hcaptcha"][src*="frame=checkbox"]',
                        'iframe[src*="hcaptcha"][src*="frame=challenge"]'):
                if page.query_selector(sel):
                    return True
        except Exception:
            pass
        return False

    def detect_captcha(self, page: Any) -> Optional[Any]:
        """Open challenges first, then unsolved checkboxes, then the inline vendors."""
        q = page.query_selector
        el = q('iframe[src*="recaptcha/api2/bframe"]')
        if self._visible(el):
            return el
        el = q('iframe[src*="hcaptcha"][src*="frame=challenge"]')
        if self._visible(el):
            return el
        el = q('iframe[src*="recaptcha/api2/anchor"]')
        if self._visible(el) and not self._is_recaptcha_anchor_checked(el):
            return el
        el = q('iframe[src*="hcaptcha"][src*="frame=checkbox"]')
        if self._visible(el) and not self._has_non_empty_field_value(page, '[name="h-captcha-response"]') \
                and not self._is_hcaptcha_anchor_checked(el):
            return el
        for sel in ('iframe[src*="challenges.cloudflare.com"]', ".cf-turnstile"):
            el = q(sel)
            if self._visible(el) and not self._has_non_empty_field_value(page, '[name="cf-turnstile-response"]'):
                return el
        for entry in VENDOR_WIDGET_LOCATORS:
            for selector in entry["selectors"]:
                el = q(selector)
                if self._visible(el):
                    return el
        return None

    def vendors_on_the_wire(self, page: Any) -> List[str]:
        """Which vendors' code the page loaded, from resource timing and linked URLs."""
        try:
            names = page.evaluate(_RESOURCES_JS)
        except Exception:
            return []
        blob = " ".join(str(n) for n in (names or []))
        return [e["puzzle_source"] for e in VENDOR_URL_MARKERS if any(h in blob for h in e["hosts"])]

    def _no_widget_message(self, page: Any) -> str:
        base = "no interactive captcha widget detected"
        loaded = self.vendors_on_the_wire(page)
        if not loaded:
            return (f"{base} (no vendor captcha code loaded on this page — likely reCAPTCHA v3 / "
                    "invisible, or a click-triggered challenge that has not been triggered)")
        return (f"{base}, BUT {'/'.join(loaded)} code IS loaded and running on this page. The "
                "vendor's markup no longer matches anything in VENDOR_WIDGET_LOCATORS — the selector "
                "list needs re-measuring against the vendor's current markup, in both solver ports")

    def is_captcha_solved(self, page: Any) -> bool:
        """The vendor's own done signal: a response token, GeeTest's banner, or a checked anchor."""
        try:
            for name in ("h-captcha-response", "g-recaptcha-response", "cf-turnstile-response"):
                if self._has_non_empty_field_value(page, f'[name="{name}"]'):
                    return True
            if self._is_geetest_accepted(page):
                return True
            hc = page.query_selector('iframe[src*="hcaptcha"][src*="frame=checkbox"]')
            if self._visible(hc) and self._is_hcaptcha_anchor_checked(hc):
                return True
            rc = page.query_selector('iframe[src*="recaptcha/api2/anchor"]')
            if self._visible(rc) and self._is_recaptcha_anchor_checked(rc):
                return True
        except Exception:
            pass
        return False

    def _is_geetest_accepted(self, page: Any) -> bool:
        try:
            return any(self._visible(el) for el in page.query_selector_all(_GEETEST_ACCEPTED_SELECTOR))
        except Exception:
            return False

    def _is_challenge_freshly_rendered(self, page: Any) -> bool:
        """A next round has painted, as opposed to the answered frame animating closed."""
        try:
            hc = page.query_selector('iframe[src*="hcaptcha"][src*="frame=challenge"]')
            if self._visible(hc):
                if self._last_submit_frame_hash and self._element_frame_hash(hc) == self._last_submit_frame_hash:
                    return False
                frame = hc.content_frame()
                prompt = frame.query_selector(".prompt-text") if frame else None
                if self._visible(prompt) and (prompt.text_content() or "").strip():
                    return True
            rc = page.query_selector('iframe[src*="recaptcha/api2/bframe"]')
            if self._visible(rc):
                frame = rc.content_frame()
                instructions = frame.query_selector(".rc-imageselect-instructions, #rc-imageselect") if frame else None
                if self._visible(instructions):
                    return True
        except Exception:
            pass
        return False

    @staticmethod
    def _banner_is_fatal_after_retry(kind: Optional[str]) -> bool:
        return kind in ("select-more", "rejected")

    def _recaptcha_banner_kind(self, page: Any) -> Optional[str]:
        """Which reCAPTCHA banner shows; `dynamic-more` is the dynamic board's normal flow, not an error."""
        try:
            bframe = page.query_selector('iframe[src*="recaptcha/api2/bframe"]')
            frame = bframe and bframe.content_frame()
            if not frame:
                return None
            for selector, kind in _RECAPTCHA_BANNERS:
                element = frame.query_selector(selector)
                if self._visible(element) and (element.text_content() or "").strip():
                    return kind
        except Exception:
            pass
        return None

    def _get_verify_button(self, frame: Any) -> Optional[Any]:
        # `.//` keeps the xpath relative so a host-page widget cannot reach the form's own submit.
        lower = "translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')"
        for text in ("verify", "next", "submit", "skip"):
            try:
                button = frame.query_selector(
                    f"xpath=.//button[contains({lower}, '{text}')] | .//div[@role='button' and contains({lower}, '{text}')]")
                if self._visible(button):
                    return button
            except Exception:
                pass
        for selector in ("#recaptcha-verify-button", ".button-submit", ".geetest_submit"):
            try:
                button = frame.query_selector(selector)
                if self._visible(button):
                    return button
            except Exception:
                pass
        return None

    # ── frames and polling ───────────────────────────────────────────────

    def _screenshot(self, element: Any, path: str, timeout_ms: Optional[int] = None,
                    animations: str = "disabled") -> None:
        """Short timeout; `disabled` freezes CSS animation, so bursts and the keyframe gate pass `allow`."""
        element.screenshot(path=path, timeout=2_500 if timeout_ms is None else timeout_ms,
                           animations=animations)

    def _element_frame_hash(self, element: Any) -> Optional[str]:
        path = _tmp_png("fh")
        try:
            self._screenshot(element, path)
            return _sha1(path)
        except Exception:
            return None
        finally:
            _unlink(path)

    def _poll(self, element: Any, timeout_ms: float, interval_ms: float,
              judge: Callable[[List[str], float], Any], where: str,
              before: Optional[Callable[[], None]] = None) -> Any:
        """Screenshot `element` every `interval_ms` until `judge(last_two_frames, elapsed_ms)` answers."""
        start = _now()
        frames: List[str] = []
        try:
            while _now() - start < timeout_ms:
                self._check_deadline(where)
                t0 = _now()
                if before is not None:
                    before()
                path = _tmp_png("poll")
                try:
                    self._screenshot(element, path)
                except Exception:
                    _unlink(path)
                    _delay(interval_ms)
                    continue
                frames.append(path)
                verdict = judge(frames, _now() - start)
                if verdict is not None:
                    return verdict
                if len(frames) > 1:
                    _unlink(frames.pop(0))
                _delay(interval_ms - (_now() - t0))
            return None
        finally:
            for path in frames:
                _unlink(path)

    def _wait_for_element_settled(self, element: Any) -> str:
        cfg = self.config
        samples: List[Tuple[float, bool]] = []

        def judge(frames, elapsed):
            if len(frames) < 2:
                return None
            samples.append((elapsed, self._has_movement(frames[0], frames[1], cfg.settle_diff_threshold)))
            verdict = settle_verdict(samples, settle_frames=cfg.settle_frames,
                                     animated_after_ms=cfg.animated_challenge_after_ms,
                                     motion_streak=cfg.animated_motion_streak)
            return None if verdict == "timeout" else verdict

        return self._poll(element, cfg.settle_timeout_ms, cfg.settle_poll_ms, judge,
                          "waiting for the challenge to settle") or "timeout"

    def _wait_for_grid_cells_loaded(self, element: Any) -> bool:
        cfg = self.config

        def judge(frames, _elapsed):
            if len(frames) < 2:
                return None
            boxes = self._find_grid(frames[1])
            states = boxes and self._grid_cell_states(frames[0], frames[1], boxes)
            return True if states and not states["empty"] and not states["changing"] and states["loaded"] else None

        return bool(self._poll(element, cfg.grid_load_timeout_ms, cfg.grid_load_poll_interval_ms,
                               judge, "waiting for grid cells to load"))

    def _wait_for_board_painted(self, element: Any) -> int:
        """A still board is not a loaded board: hold until the panel's centre carries structure."""
        cfg = self.config
        start = _now()
        painted = self._poll(element, cfg.board_paint_timeout_ms, cfg.board_paint_poll_ms,
                             lambda frames, _e: True if self._board_painted(frames[-1]) is not False else None,
                             "waiting for the board to paint")
        waited = int(_now() - start)
        if not painted:
            _log(f"[board] the widget never painted a puzzle in {cfg.board_paint_timeout_ms}ms")
        elif waited >= cfg.board_paint_poll_ms:
            _log(f"[board] waited {waited}ms for the widget to paint its puzzle")
        return waited

    def _wait_for_change_since(self, element: Any, since_hash: str) -> bool:
        start = _now()
        while _now() - start < self.config.post_submit_change_timeout_ms:
            current = self._element_frame_hash(element)
            if current and current != since_hash:
                return True
            _delay(self.config.settle_poll_ms)
        return False

    def _wait_for_hcaptcha_challenge_images(self, challenge_iframe: Any) -> None:
        try:
            frame = challenge_iframe.content_frame()
            if frame:
                frame.wait_for_function(_HCAPTCHA_IMAGES_READY_JS, timeout=self.config.hcaptcha_images_timeout_ms)
        except Exception:
            pass

    def _get_grid_boxes(self, element: Any) -> Optional[Dict[str, Any]]:
        path = _tmp_png("findgrid")
        try:
            self._screenshot(element, path, timeout_ms=self.config.element_screenshot_timeout_ms)
            boxes = self._find_grid(path)
            dims = _read_png_dimensions(path)
            if not boxes or len(boxes) not in (9, 16) or not dims:
                return None
            return {"boxes": [list(b) for b in boxes], "size": 4 if len(boxes) == 16 else 3,
                    "screenshot_w": dims[0], "screenshot_h": dims[1]}
        except Exception:
            return None
        finally:
            _unlink(path)

    def _frame_changed_since(self, element: Any, prior_path: str, threshold: float) -> bool:
        probe = _tmp_png("freshcheck")
        try:
            self._screenshot(element, probe)
            return self._has_movement(prior_path, probe, threshold)
        except Exception:
            return False
        finally:
            _unlink(probe)

    def _solve_frame_freshness_guarded(self, element: Any, initial_shot: str,
                                       run_query: Callable[[str], Tuple[List[CaptchaAction], List[Dict[str, Any]]]]):
        """Re-solve when the frame changed during inference; twice means the board cycles."""
        cfg = self.config
        owned: List[str] = []
        merged: List[Dict[str, Any]] = []
        try:
            current = initial_shot
            actions, usage = run_query(current)
            merged.extend(usage)
            if not cfg.stale_frame_resolve_enabled:
                return actions, merged
            if self._frame_changed_since(element, current, _MOVED_DURING_INFERENCE_DIFF):
                self._arm_animated_probe()
                _log("[freshness] the widget moved while the model was reading it")
            changes = 0
            for attempt in range(cfg.max_stale_frame_resolves):
                if not self._frame_changed_since(element, current, cfg.stale_frame_diff_threshold):
                    break
                changes += 1
                if changes >= 2:
                    self._arm_animated_probe()
                    _log("[freshness] the frame changed twice during inference — this board cycles")
                    return actions, merged
                self._wait_for_board_painted(element)
                fresh = _tmp_png("freshsolve")
                try:
                    self._screenshot(element, fresh)
                except Exception:
                    _unlink(fresh)
                    break
                owned.append(fresh)
                _log(f"[freshness] frame changed during inference (re-solve {attempt + 1}/{cfg.max_stale_frame_resolves})")
                current = fresh
                actions, usage = run_query(current)
                merged.extend(usage)
            return actions, merged
        finally:
            for path in owned:
                _unlink(path)

    # ── animated challenges ──────────────────────────────────────────────

    def _settle_or_animated(self, element: Any) -> bool:
        """Wait for the widget to settle; True routes the caller to the recording path."""
        if self._known_animated:
            return True
        with self._phase("settle"):
            verdict = self._wait_for_element_settled(element)
        if verdict != "animated":
            # 'settled' is not proof of static; a repeated answer arms one recording to find out.
            if not self._animated_probe_armed or self._animated_probe_done:
                return False
            self._animated_probe_done, self._animated_probe_armed = True, False
            _log("[animated] the same answer came back twice — recording the challenge")
        self._known_animated = True
        if not self.config.video_solve_enabled:
            raise AnimatedChallengeError("the challenge never settles and video_solve_enabled is off")
        _log("[animated] challenge is animated — solving it from keyframes")
        return True

    def _arm_animated_probe(self) -> None:
        if self.config.animated_probe_enabled and not self._animated_probe_done:
            self._animated_probe_armed = True

    def _should_speculate(self, puzzle_source: str, text_mode: bool) -> bool:
        cfg = self.config
        return (cfg.video_solve_enabled and cfg.speculative_burst_enabled and not self._acted_on_board
                and puzzle_source != "recaptcha" and not text_mode)

    def _burst(self, element: Any) -> Tuple[List[Any], List[str], bool, float]:
        """Film the widget until a screen comes back (a cycle) or nothing new appears for a floor window.

        Returns `(frames, distinct_digests, cycle_closed, elapsed_ms)`. Both bounds are wall-clock.
        """
        import cv2

        cfg = self.config
        floor_ms = float(cfg.video_burst_duration_ms)
        max_ms = max(floor_ms, float(cfg.video_burst_max_ms))
        interval = 1.0 / max(1, int(cfg.video_burst_fps))
        frames: List[Any] = []
        order: List[str] = []
        last_digest: Optional[str] = None
        cycle_closed = False
        last_new_ms = 0.0
        shot = _tmp_png("burst")
        t0 = time.monotonic()
        hang_deadline = t0 * 1000.0 + burst_hang_deadline_ms(cfg)
        try:
            while (time.monotonic() - t0) * 1000.0 < max_ms:
                if time.monotonic() * 1000.0 > hang_deadline:
                    raise CaptchaSolveError(
                        f"the animated recording stalled: {len(frames)} frames in "
                        f"{burst_hang_deadline_ms(cfg):.0f}ms. The widget is not screenshotting.")
                start = time.monotonic()
                try:
                    self._screenshot(element, shot, animations="allow")
                    img = cv2.imread(shot)
                except Exception as exc:
                    _debug(f"burst frame failed: {exc}")
                    img = None
                if img is not None:
                    frames.append(img)
                    d = _sha1(shot)
                    if d != last_digest:
                        if d in order and len(order) >= 2:
                            cycle_closed = True
                        elif d not in order:
                            order.append(d)
                            last_new_ms = (time.monotonic() - t0) * 1000.0
                        last_digest = d
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                if cycle_closed and elapsed_ms >= floor_ms:
                    _log(f"[animated] cycle closed after {elapsed_ms / 1000:.1f}s ({len(order)} screens)")
                    break
                if elapsed_ms >= floor_ms and elapsed_ms - last_new_ms >= floor_ms:
                    _log(f"[animated] no new screen for {floor_ms / 1000:.1f}s ({len(order)} seen) — settled")
                    break
                wait = interval - (time.monotonic() - start)
                if wait > 0 and elapsed_ms + wait * 1000.0 < max_ms:
                    time.sleep(wait)
        finally:
            _unlink(shot)
        return frames, order, cycle_closed, (time.monotonic() - t0) * 1000.0

    def _slice(self, frames: List[Any], burst_ms: float) -> Tuple[List[str], str]:
        """Cut a burst with the training-side slicer; the model answers with a frame number into it."""
        from .keyframes import extract_keyframes, write_keyframes

        kfset = extract_keyframes(frames, fps=measured_fps(len(frames), burst_ms, self.config.video_burst_fps))
        self._keyframe_mode = kfset.mode
        self._keyframe_steady_screens = kfset.steady_screens
        temp_dir = tempfile.mkdtemp(prefix="ck_keyframes_")
        paths = [str(p) for p in write_keyframes(kfset, temp_dir, stem="challenge")]
        _log(f"[animated] sliced to {len(paths)} keyframe(s) (mode={kfset.mode})")
        return paths, temp_dir

    def _record_keyframes(self, element: Any) -> Tuple[List[str], str]:
        """Record the widget and return `(keyframe_paths, temp_dir)`; the caller removes the dir."""
        cfg = self.config
        self._grant_video_budget()
        if self._deadline_ms is not None and self._deadline_ms - _now() < cfg.video_burst_duration_ms:
            raise CaptchaSolveError(
                f"only {self._deadline_ms - _now():.0f}ms of the {cfg.overall_solve_timeout_ms}ms solve "
                f"budget is left and an animated recording needs {cfg.video_burst_duration_ms}ms — not "
                "starting one that would be cut off mid-way. Raise overall_solve_timeout_ms or "
                "video_extra_inference_ms, or set video_solve_enabled=False.")
        frames, _order, _closed, burst_ms = self._burst(element)
        if not frames:
            raise AnimatedChallengeError("could not record the animated challenge (no frame screenshotted)")
        _log(f"[animated] recorded {len(frames)} frames in {burst_ms / 1000:.1f}s "
             f"({measured_fps(len(frames), burst_ms, cfg.video_burst_fps):.1f}fps)")
        return self._slice(frames, burst_ms)

    def _speculate(self, element: Any, shot: str, puzzle_source: str, retry_mode: Optional[str],
                   text_mode: bool) -> Tuple[List[CaptchaAction], List[Dict[str, Any]], Optional[str]]:
        """Ask the still and film the widget at once; `keyframe_dir` is None when the board is still.

        Only the HTTP request crosses to the worker thread: sync Playwright is thread-affine.
        """
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(self._get_solution, shot, puzzle_source, retry_mode, text_mode)
            with self._phase("burst"):
                frames, order, cycle_closed, burst_ms = self._burst(element)
            _log(f"[animated] burst verdict after {burst_ms / 1000:.1f}s: "
                 f"{'CYCLING' if cycle_closed else 'not cycling'} ({len(order)} screens)")
            if not cycle_closed:
                with self._phase("inference"):
                    actions, usage = fut.result()
                if len(order) > 1:
                    # Moved once and settled: re-read the screen it came to rest on.
                    try:
                        self._screenshot(element, shot)
                        actions, extra = self._get_solution(shot, puzzle_source, retry_mode, text_mode)
                        usage = list(usage) + list(extra)
                    except Exception as exc:
                        _debug(f"settled re-read failed: {exc}")
                return actions, usage, None
            _log("[animated] the widget moved while the model was reading it — finishing the recording")
            fut.result()
            self._grant_video_budget()
            paths, keyframe_dir = self._slice(frames, burst_ms)
            with self._phase("inference"):
                actions, usage = self._get_keyframe_solution(paths)
            self._animated_plan = (paths, keyframe_dir, actions, usage)
            return actions, usage, keyframe_dir
        finally:
            pool.shutdown(wait=True)

    def _invalidate_animated_answer(self) -> None:
        """Keep the frames and drop the answer, unless the clip has no steady screen to re-ask."""
        plan = self._animated_plan
        if plan is None or plan[2] is None:
            return
        if self._keyframe_steady_screens < 2:
            self._discard_animated_plan()
            self._keyframe_mode, self._keyframe_steady_screens = None, 0
            return
        self._animated_plan = (plan[0], plan[1], None, None)

    def _discard_animated_plan(self) -> None:
        plan = self._animated_plan
        self._animated_plan = None
        if plan and plan[1]:
            shutil.rmtree(plan[1], ignore_errors=True)

    def _answer_region_recurs(self, keyframe_path: str, ref: Any, box: Any) -> bool:
        """Does the chosen keyframe's answer area appear in a sibling keyframe of the same clip?"""
        import cv2

        from .keyframes import MATCH_REGION_TOLERANCE, region_diff_ratio

        folder, base = os.path.dirname(keyframe_path), os.path.basename(keyframe_path)
        prefix = re.sub(r"_\d+\.png$", "_", base, flags=re.IGNORECASE)
        if prefix == base:
            return False
        try:
            siblings = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
                        if f.startswith(prefix) and f.lower().endswith(".png") and f != base]
        except OSError:
            return False
        for other in siblings:
            img = cv2.imread(other)
            if img is not None and img.shape == ref.shape and region_diff_ratio(ref, img, box) <= MATCH_REGION_TOLERANCE:
                return True
        return False

    def _wait_for_keyframe(self, element: Any, keyframe_path: str, point_norm: Tuple[float, float]) -> bool:
        """Hold until the widget looks like the keyframe around the action point, or the wait is spent."""
        import cv2

        from .keyframes import MATCH_REGION_TOLERANCE, region_box, region_diff_ratio

        ref = cv2.imread(keyframe_path)
        if ref is None:
            return False
        box = region_box(ref.shape[1::-1], point_norm)
        steady = self._keyframe_steady_screens >= 2
        if not steady and not self._answer_region_recurs(keyframe_path, ref, box):
            _log("[animated] the answer area is unique to the chosen frame; acting without waiting")
            return False
        cfg = self.config
        wait_ms = cfg.keyframe_wait_timeout_ms if steady else min(cfg.keyframe_wait_timeout_ms, cfg.video_burst_duration_ms)
        deadline = _now() + wait_ms
        probe = _tmp_png("kfwait")
        best, polls = 1.0, 0
        try:
            while _now() < deadline:
                self._check_deadline("waiting for the challenge keyframe")
                try:
                    self._screenshot(element, probe, animations="allow")
                    live = cv2.imread(probe)
                except Exception:
                    live = None
                if live is not None:
                    d = region_diff_ratio(ref, live, box)
                    best = min(best, d)
                    if d <= MATCH_REGION_TOLERANCE:
                        _log(f"[animated] widget matched the chosen keyframe (diff={d:.4f})")
                        return True
                    polls += 1
                    if polls >= _NOT_THIS_BOARD_POLLS and best > _NOT_THIS_BOARD_DIFF:
                        _log("[animated] the widget no longer resembles the recorded board")
                        self._discard_animated_plan()
                        return False
                _delay(cfg.keyframe_wait_poll_ms)
        finally:
            _unlink(probe)
        self._discard_animated_plan()
        _log(f"[animated] widget never matched the chosen keyframe within {wait_ms}ms (closest diff={best:.4f})")
        return False

    # ── reCAPTCHA 3x3 dynamic driver ─────────────────────────────────────

    def _cell_center_page(self, cell: int, session: _GridSession) -> Tuple[float, float]:
        x1, y1, x2, y2 = session.grid_boxes[cell - 1]
        return (session.element_box["x"] + (x1 + x2) / 2 * session.scale_x,
                session.element_box["y"] + (y1 + y2) / 2 * session.scale_y)

    def _hover_cell(self, page: Any, session: _GridSession, cell: int) -> None:
        if not self._human.hovers:
            return
        first = session.grid_boxes[0]
        cx, cy = self._cell_center_page(cell, session)
        self._smooth_move(page,
                          cx + (random.random() - 0.5) * (first[2] - first[0]) * session.scale_x * 0.4,
                          cy + (random.random() - 0.5) * (first[3] - first[1]) * session.scale_y * 0.4)

    @staticmethod
    def _bbox_to_cell(bbox: Sequence[float], grid_boxes: Sequence[Sequence[int]],
                      width: int, height: int) -> Optional[int]:
        cx = (float(bbox[0]) + float(bbox[2])) / 2 * width
        cy = (float(bbox[1]) + float(bbox[3])) / 2 * height
        for index, (x1, y1, x2, y2) in enumerate(grid_boxes):
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                return index + 1
        return None

    @staticmethod
    def _order_by_priority(loading: Sequence[int], priority: Sequence[int]) -> List[int]:
        remaining = set(loading)
        ordered = [c for c in priority if c in remaining and not remaining.discard(c)]
        return ordered + sorted(remaining)

    def _hover_loop(self, page: Any, session: _GridSession, cells: Sequence[int]) -> Optional[Callable[[], None]]:
        if not cells:
            return None
        cycle = itertools.cycle(cells)

        def hover():
            try:
                self._hover_cell(page, session, next(cycle))
            except Exception:
                pass

        return hover

    def _watch_clicked_tiles(self, page: Any, element: Any, session: _GridSession,
                             priority: Sequence[int] = ()) -> Tuple[List[int], bool]:
        """`(loading, chipped)`: a chip means the photo was kept, a blank or fade means it is being swapped."""
        cfg = self.config
        watch = set(priority) or None

        def judge(frames, _elapsed):
            if len(frames) < 2:
                return None
            states = self._grid_cell_states(frames[0], frames[1], session.grid_boxes) or {}
            if priority and set(states.get("selected", [])).issuperset(priority):
                return [], True
            loading = sorted({c for c in states.get("empty", []) + states.get("changing", [])
                              if watch is None or c in watch})
            return (self._order_by_priority(loading, priority), False) if loading else None

        return self._poll(element, cfg.recaptcha_fade_onset_grace_ms, cfg.recaptcha_dynamic_fade_poll_ms,
                          judge, "watching for the tile refresh",
                          self._hover_loop(page, session, priority)) or ([], False)

    def _wait_for_any_clicked_tile_loaded(self, page: Any, element: Any, session: _GridSession,
                                          fading_cells: Sequence[int]) -> bool:
        if not fading_cells:
            return True
        cfg = self.config

        def judge(frames, _elapsed):
            if len(frames) < 2:
                return None
            states = self._grid_cell_states(frames[0], frames[1], session.grid_boxes)
            return True if states and any(c in states["loaded"] for c in fading_cells) else None

        hover = self._hover_loop(page, session, fading_cells) if cfg.recaptcha_tile_hover_enabled else None
        return bool(self._poll(element, cfg.recaptcha_dynamic_fade_wait_ms, cfg.recaptcha_dynamic_fade_poll_ms,
                               judge, "waiting for a tile to reload", hover))

    def _solve_recaptcha_grid(self, page: Any, element: Any, retry_mode: Optional[str],
                              grid: Dict[str, Any], element_box: Dict[str, float]) -> Tuple[bool, List[Dict[str, Any]]]:
        """Click, watch the clicked tiles, re-solve while they swap, submit on `done` or a chipped board."""
        cfg = self.config
        session = _GridSession(grid["boxes"], element_box,
                               element_box["width"] / grid["screenshot_w"],
                               element_box["height"] / grid["screenshot_h"],
                               grid["screenshot_w"], grid["screenshot_h"])
        clicked_order: List[int] = []
        performed = should_submit = False
        all_usage: List[Dict[str, Any]] = []
        pending_retry = retry_mode

        for round_index in range(1, cfg.recaptcha_max_dynamic_rounds + 1):
            self._check_deadline(f"recaptcha grid round {round_index}")
            if round_index > 1:
                with self._phase("grid-load"):
                    self._wait_for_grid_cells_loaded(element)
            shot = _tmp_png("recap")
            try:
                with self._phase("screenshot"):
                    self._screenshot(element, shot, timeout_ms=cfg.element_screenshot_timeout_ms)
                retry_for_round, pending_retry = pending_retry, None
                with self._phase("inference"):
                    actions, usage = self._solve_frame_freshness_guarded(
                        element, shot, lambda p: self._get_solution(p, "recaptcha", retry_for_round))
                all_usage.extend(usage)
                action = _as_dict(actions[0]) if actions else None
            finally:
                _unlink(shot)

            kind = (action or {}).get("action")
            if not action or kind == "done":
                _log(f"[recaptcha-grid] round {round_index}: done; submitting.")
                should_submit = True
                break
            if kind == "wait":
                with self._phase("fade-wait"):
                    loading, _ = self._watch_clicked_tiles(page, element, session, clicked_order)
                    self._wait_for_any_clicked_tile_loaded(page, element, session, loading)
                continue
            if kind != "click":
                _log(f"[recaptcha-grid] round {round_index}: unexpected action; re-solving.")
                continue
            bboxes = action.get("target_bounding_boxes") or (
                [action["target_bounding_box"]] if action.get("target_bounding_box") else [])
            if not bboxes:
                _delay(500)
                continue
            clicked_this_round: List[int] = []
            for bbox in bboxes:
                cell = self._bbox_to_cell(bbox, session.grid_boxes, session.screenshot_w, session.screenshot_h)
                self._execute_click(page, {"target_bounding_box": bbox}, element_box)
                if cell is not None:
                    clicked_order.append(cell)
                    clicked_this_round.append(cell)
                self._human.pause("between")
            performed = True
            _log(f"[recaptcha-grid] round {round_index}: clicked cells {clicked_this_round}.")
            with self._phase("fade-wait"):
                loading, chipped = self._watch_clicked_tiles(page, element, session, clicked_this_round)
            if chipped or not loading:
                _log(f"[recaptcha-grid] round {round_index}: {'tiles chipped' if chipped else 'nothing loading'}; submitting.")
                should_submit = True
                break
            with self._phase("fade-wait"):
                self._wait_for_any_clicked_tile_loaded(page, element, session, loading)

        if should_submit:
            frame = element.content_frame()
            verify = frame and self._get_verify_button(frame)
            if verify:
                _log("[recaptcha-grid] clicking Verify to submit.")
                self._move_and_click(page, verify)
                performed = True
        return performed, all_usage

    # ── one pass over a rendered challenge ───────────────────────────────

    def _solve_single(self, page: Any, element: Any, retry_mode: Optional[str]) -> Tuple[bool, List[Dict[str, Any]]]:
        try:
            src = element.get_attribute("src") or ""
        except Exception:
            src = ""
        puzzle_source = vendor_from_src(src)
        scope = element.content_frame() or element
        # Only the DOM can tell a typed captcha from a click puzzle; hCaptcha and reCAPTCHA never type.
        text_mode = puzzle_source not in VENDORS_WITH_BESPOKE_HANDLING and self._answer_box(scope, element) is not None
        if text_mode:
            _log("widget has a text box; solving as a distorted-text captcha")

        is_animated = False
        if puzzle_source == "hcaptcha" and "frame=challenge" in src:
            if self._last_submit_frame_hash:
                with self._phase("await-next-round"):
                    self._wait_for_change_since(element, self._last_submit_frame_hash)
                self._last_submit_frame_hash = None
            with self._phase("hcaptcha-images"):
                self._wait_for_hcaptcha_challenge_images(element)
            is_animated = self._settle_or_animated(element)
            if self.is_captcha_solved(page):
                _log("solved while waiting for the next round; skipping inference.")
                return False, []
        elif puzzle_source not in VENDORS_WITH_BESPOKE_HANDLING:
            is_animated = self._settle_or_animated(element)

        if puzzle_source == "recaptcha" and "recaptcha/api2/bframe" in src:
            with self._phase("grid-load"):
                self._wait_for_grid_cells_loaded(element)
            grid = self._get_grid_boxes(element)
            if grid and grid["size"] == 3:
                element_box = element.bounding_box()
                if element_box:
                    return self._solve_recaptcha_grid(page, element, retry_mode, grid, element_box)

        with self._phase("board-paint"):
            self._wait_for_board_painted(element)

        shot = _tmp_png("captcha")
        performed = slid = answered = have_shot = False
        all_usage: List[Dict[str, Any]] = []
        keyframe_dir: Optional[str] = None
        try:
            if is_animated:
                if self._animated_plan is not None:
                    keyframes, keyframe_dir, actions, all_usage = self._animated_plan
                    reused = actions is not None
                    _log("[animated] reusing the recorded answer" if reused
                         else "[animated] re-asking on the frames already recorded")
                else:
                    reused = False
                    with self._phase("burst"):
                        keyframes, keyframe_dir = self._record_keyframes(element)
                if len(keyframes) < 2:
                    _log("[animated] the recording shows one picture; solving it as a still")
                    is_animated = False
                    shutil.copyfile(keyframes[0], shot)
                    have_shot = True
                    self._discard_animated_plan()
                    shutil.rmtree(keyframe_dir, ignore_errors=True)
                    keyframe_dir = None
                elif not reused:
                    with self._phase("inference"):
                        actions, all_usage = self._get_keyframe_solution(keyframes)
                    self._animated_plan = (keyframes, keyframe_dir, actions, all_usage)

            if not is_animated:
                if not have_shot:
                    with self._phase("screenshot"):
                        self._screenshot(element, shot, timeout_ms=self.config.element_screenshot_timeout_ms)
                if self._should_speculate(puzzle_source, text_mode):
                    actions, all_usage, keyframe_dir = self._speculate(element, shot, puzzle_source, retry_mode, text_mode)
                    is_animated = keyframe_dir is not None
                else:
                    with self._phase("inference"):
                        actions, all_usage = self._solve_frame_freshness_guarded(
                            element, shot,
                            lambda p: self._get_solution(p, puzzle_source, retry_mode, text_mode=text_mode))

            element_box = element.bounding_box()
            if not element_box and answer_needs_element_box(actions):
                raise CaptchaSolveError("could not get bounding box of captcha element")

            _log("[answer] " + json.dumps({"actions": [_as_dict(a) for a in actions]}, default=str))
            self._note_answer(actions, retry_mode)
            _log(f"executing {len(actions)} action(s)")
            frame = element.content_frame()

            for raw_action in actions:
                self._check_deadline("action execution")
                action = _as_dict(raw_action)
                kind = action.get("action")
                await_kf = action.get("await_keyframe")
                if kind == "click":
                    bboxes = action.get("target_bounding_boxes") or (
                        [action["target_bounding_box"]] if action.get("target_bounding_box") else [])
                    if not bboxes and not action.get("target_coordinates"):
                        _log("click action has no bboxes or coordinates; skipping")
                        continue
                    for bbox in bboxes or [None]:
                        one = {"target_bounding_box": bbox} if bbox else action
                        self._execute_click(page, one, element_box, element, await_kf)
                        if bbox:
                            self._human.pause("between")
                    performed = answered = True
                elif kind == "drag" and not action.get("source_bounding_box"):
                    if self._execute_slide(page, element, scope, action, element_box):
                        performed = slid = True
                elif kind == "drag":
                    if await_kf:
                        self._wait_for_keyframe(element, await_kf, _bbox_center(action["source_bounding_box"]))
                    self._execute_drag(page, action, element_box)
                    performed = answered = True
                elif kind == "type":
                    if self._execute_type(page, scope, action, element):
                        performed = answered = True
                elif kind == "wait":
                    duration = int(action.get("duration_ms") or 0)
                    if duration > 0:
                        _delay(duration)
                        performed = True

            # A slide submits itself on release; an empty or `done` plan still presses Verify/Skip.
            lookup = frame or (scope if not slid else None)
            verify_button = lookup and self._get_verify_button(lookup)
            if not slid and (answered or not performed) and verify_button:
                _log(f"clicking Verify to submit ({puzzle_source}).")
                self._move_and_click(page, verify_button)
                performed = True
                self._last_submit_frame_hash = self._element_frame_hash(element)
        finally:
            _unlink(shot)
            held = self._animated_plan[1] if self._animated_plan else None
            if keyframe_dir and keyframe_dir != held:
                shutil.rmtree(keyframe_dir, ignore_errors=True)
        return performed, all_usage

    # ── public entry point ───────────────────────────────────────────────

    def watch(self, page: Any, **options: Any) -> "CaptchaWatcher":
        """A watcher over `page`; `.run()` blocks, `.poll_once()` is cooperative."""
        from .watcher import CaptchaWatcher

        return CaptchaWatcher(solver=self, page=page, **options)

    def solve(self, page: Any) -> SolveResult:
        start = _now()
        usage: List[Dict[str, Any]] = []
        self._last_submit_frame_hash = None
        self._deadline_ms = start + self.config.overall_solve_timeout_ms
        self._human.reset(page)
        self._reset_animated_state()
        self._budget = PhaseBudget()
        # One session id per solve groups its inference rounds into one billable attempt.
        previous_session = os.environ.get(_SESSION_ENV)
        session_id = os.environ[_SESSION_ENV] = str(uuid.uuid4())
        solved = False
        try:
            result = self._solve_impl(page, start, usage)
            result.phases = dict(self._budget.totals, total=self._budget.elapsed_ms())
            solved = bool(result.is_solved)
            return result
        finally:
            if timings_enabled():
                print(self._budget.report(), file=sys.stderr)
            self._deadline_ms = None
            try:
                self._solver.planner.report_outcome(session_id, solved)
            except Exception as exc:
                _log(f"[outcome] could not report: {exc}")
            if previous_session is None:
                os.environ.pop(_SESSION_ENV, None)
            else:
                os.environ[_SESSION_ENV] = previous_session

    def _solve_impl(self, page: Any, start: float, usage: List[Dict[str, Any]]) -> SolveResult:
        cfg = self.config
        pending_retry_mode: Optional[str] = None
        retried_underselect = False
        unsupported_retries = stale_retries = render_waits = 0
        has_interacted = False
        max_render_waits = min(6, cfg.max_solve_loops - 1)

        def done() -> SolveResult:
            return SolveResult(True, self._last_mouse, _aggregate(usage))

        for attempt in range(1, cfg.max_solve_loops + 1):
            if attempt >= 2:
                self._arm_animated_probe()
            if _now() - start > cfg.overall_solve_timeout_ms:
                raise CaptchaSolveError(
                    f"captcha solve timed out after {cfg.overall_solve_timeout_ms}ms (attempt {attempt}/{cfg.max_solve_loops})")
            if has_interacted and self.is_captcha_solved(page):
                _log("captcha reports solved; finishing.")
                return done()

            with self._phase("detect"):
                element = self.detect_captcha(page)
            if not element:
                if has_interacted:
                    _log("no supported captcha remains after interaction; considering solved.")
                    return done()
                if self.is_captcha_solved(page):
                    _log("captcha already satisfied; nothing to solve.")
                    return done()
                if self.has_interactive_widget_in_dom(page) and render_waits < max_render_waits:
                    render_waits += 1
                    _log(f"widget in DOM but not yet rendered; waiting ({render_waits}/{max_render_waits}).")
                    _delay(800 + random.random() * 300)
                    continue
                raise NoCaptchaFoundError(self._no_widget_message(page))

            _log(f"--- captcha solve loop {attempt}/{cfg.max_solve_loops} ---")
            retry_mode, pending_retry_mode = pending_retry_mode, None
            try:
                did_interact, round_usage = self._solve_single(page, element, retry_mode)
            except AnimatedChallengeError:
                raise
            except UnsupportedCaptchaError as unsupported:
                # Mid-solve, a transitional blank frame reads as unsupported; settle and retry.
                if has_interacted and unsupported_retries < cfg.max_unsupported_resolves:
                    unsupported_retries += 1
                    current = self.detect_captcha(page)
                    with self._phase("settle"):
                        settled = self._wait_for_element_settled(current)
                    if current and settled == "animated" and not cfg.video_solve_enabled:
                        raise AnimatedChallengeError("the challenge never settles and video_solve_enabled is off")
                    _log(f'"unsupported" mid-solve; retrying ({unsupported_retries}/{cfg.max_unsupported_resolves}).')
                    continue
                raise UnsupportedChallengeError(f"cannot solve this kind of captcha — {unsupported}") from unsupported
            except Exception as exc:
                message = str(exc)
                closed = bool(_CLOSED_TARGET_RE.search(message))
                if has_interacted and (closed or _STALE_HANDLE_RE.search(message)):
                    # The handle most often went stale because the answer was accepted.
                    try:
                        if self.is_captcha_solved(page):
                            _log("captcha reports solved; finishing.")
                            return done()
                    except Exception:
                        pass
                    if closed:
                        raise PageClosedError(
                            "the page, context or browser closed mid-solve, after the answer had been "
                            "submitted but before the vendor's verdict could be read — the solve may in "
                            "fact have succeeded") from exc
                    if stale_retries < cfg.max_stale_element_retries:
                        stale_retries += 1
                        _log(f"stale challenge handle after submit; re-detecting ({stale_retries}/{cfg.max_stale_element_retries}).")
                        _delay(cfg.stale_element_backoff_ms)
                        continue
                raise

            has_interacted = has_interacted or did_interact
            if self._no_progress_rounds >= cfg.max_no_progress_rounds:
                raise CaptchaSolveError(
                    f"no progress: the model returned the same answer {self._no_progress_rounds + 1} times "
                    f"running and the challenge is still up (attempt {attempt}/{cfg.max_solve_loops})")
            render_waits = 0
            usage.extend(round_usage)

            # One polled wait per round: the vendor's verdict, the widget going away, or a fresh board.
            window_ms = (cfg.post_solve_outcome_timeout_ms if did_interact
                         else cfg.post_solve_delay_ms + random.random() * 300)
            deadline = _now() + window_ms
            t0 = time.perf_counter()
            solved = False
            widget_gone = 0
            while _now() < deadline:
                if self.is_captcha_solved(page):
                    solved = True
                    break
                widget_gone = widget_gone + 1 if self.detect_captcha(page) is None else 0
                if widget_gone >= 2:
                    solved = True
                    break
                if self._is_challenge_freshly_rendered(page):
                    self._fresh_board()
                    break
                _delay(cfg.post_solve_outcome_poll_ms)
            verdict_ms = (time.perf_counter() - t0) * 1000.0
            if self._budget is not None:
                self._budget.add("await-verdict" if did_interact else "post-submit-delay", verdict_ms)
            if solved:
                _log(f"[verdict] success signal arrived after {verdict_ms:.0f}ms")
                return done()

            if self._banner_is_fatal_after_retry(self._recaptcha_banner_kind(page)):
                if retried_underselect:
                    raise CaptchaSolveError(
                        "reCAPTCHA still showing the under-selection error after retry; aborting "
                        "(model unable to identify the missed tile)")
                _log("reCAPTCHA under-selection error; retrying with missed-tiles prompt.")
                pending_retry_mode, retried_underselect = "missed-tiles", True

            if not self.detect_captcha(page):
                return done()
            if not did_interact:
                raise CaptchaSolveError(
                    "captcha still detected but the solver performed no interactions; aborting to avoid an infinite loop")

        raise CaptchaSolveError(f"captcha still detected after {cfg.max_solve_loops} solve loops")


def _bbox_center(bbox: Sequence[float]) -> Tuple[float, float]:
    x1, y1, x2, y2 = (float(v) for v in bbox)
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def _round_pts(value: Any, places: int = 3) -> Any:
    if isinstance(value, (int, float)):
        return round(float(value), places)
    if isinstance(value, (list, tuple)):
        return [_round_pts(v, places) for v in value]
    return value


def _as_dict(action: Any) -> Dict[str, Any]:
    if isinstance(action, dict):
        return action
    for method in ("model_dump", "dict"):
        if hasattr(action, method):
            try:
                return getattr(action, method)()
            except Exception:
                pass
    return {"action": getattr(action, "action", None)}


def _aggregate(usage: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not usage:
        return []
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for entry in usage:
        for key in total:
            total[key] += int(entry.get(key) or 0)
    return [{"rounds": len(usage), **total}]


def _read_png_dimensions(path: str) -> Optional[Tuple[int, int]]:
    """Width and height from the IHDR chunk, so no image-size dependency is needed."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
        if len(header) < 24 or header[1:4] != b"PNG":
            return None
        width, height = int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")
        return (width, height) if width and height else None
    except OSError:
        return None


def solve_captcha_on_page(page: Any, **kwargs: Any) -> SolveResult:
    return PageSolver(**kwargs).solve(page)
