"""
Browser page driver — the Python mirror of `js/src/solver.ts`.

WHAT THIS IS
The Python port used to be image-in / actions-out: you handed `CaptchaSolver` a
PNG and got back click/drag actions, and something else had to own the browser.
Everything that actually drives a page — finding the challenge iframe, waiting
for tiles to paint, clicking, submitting, deciding whether the vendor accepted
— lived only in the TypeScript driver. This module closes that gap so a Python
caller (camoufox's Python API, plain Playwright, patchright) can solve a captcha
end to end without a Node process.

THE SPLIT, WHICH IS THE SAME ON BOTH SIDES
  vision / CV / prompting  -> Python (`solver.py`, `planner.py`, `tool_calls/`)
  page driving + clicking  -> the driver (this file, or solver.ts)

The TS driver reaches the Python half by spawning the CLI and talking JSON over
a pipe. This driver calls the very same functions in-process — no subprocess, no
serialisation, no persistent CV worker to leak. That is the only intended
difference between the two drivers: everything about WHAT to click, WHEN a frame
is settled, and WHETHER a puzzle is supported is shared code, so the two cannot
drift on the parts that decide accuracy.

STRUCTURAL TYPING, NO BROWSER DEPENDENCY
Like `playwright-types.ts`, this module imports no browser package. The caller
passes whatever Playwright-compatible `page` they have; we duck-type the slice
we use. Importing `playwright` here would force it into every consumer's tree
and break across version skew, and camoufox users already have their own.

SYNC ONLY, FOR NOW
This mirrors the synchronous Playwright API (`Camoufox()`, `sync_playwright()`),
which is camoufox's headline Python interface. An async mirror is mechanical but
is NOT written yet — see `solve_captcha_on_page`'s docstring. Calling this from
an async event loop will not work, because sync Playwright handles cannot be
driven from one.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .action_types import CaptchaAction
from .solver import CaptchaSolver, UnsupportedCaptchaError
from .trajectory import generate_trajectory

DEBUG = os.getenv("CAPTCHA_DEBUG", "0") == "1"

# Read by planner.py, which turns it into the X-CK-Session header. Kept as a
# module constant so the name is defined in exactly one place per port.
_SESSION_ENV = "CAPTCHA_KRAKEN_SESSION"


def _log(message: str) -> None:
    print(f"[captchakraken] {message}")


def _debug(message: str) -> None:
    if DEBUG:
        print(f"[captchakraken:debug] {message}")


def _delay(ms: float) -> None:
    if ms > 0:
        time.sleep(ms / 1000.0)


def _tmp_png(prefix: str) -> str:
    fd, path = tempfile.mkstemp(prefix=f"{prefix}_", suffix=".png")
    os.close(fd)
    return path


def _unlink(path: Optional[str]) -> None:
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except OSError:
            pass  # best-effort; a leaked temp frame is not worth failing a solve


# --------------------------------------------------------------------------
# Errors — the taxonomy the outer loop branches on. The TS driver tags plain
# Errors with `.animated` / `.unsupported` booleans; Python gets real types,
# which is the same distinction expressed properly.
# --------------------------------------------------------------------------


class CaptchaSolveError(Exception):
    """Base for every failure this driver raises."""


class AnimatedChallengeError(CaptchaSolveError):
    """The challenge never stops moving — a video/animated puzzle we can't read."""


class UnsupportedChallengeError(CaptchaSolveError):
    """A settled frame the model reports it cannot solve (e.g. hCaptcha click/drag)."""


class NoCaptchaFoundError(CaptchaSolveError):
    """No interactive widget — reCAPTCHA v3/invisible, or a click-triggered challenge."""


# Playwright surfaces these as messages, not types, so both drivers match on
# text. Kept as one pattern so the two lists can be diffed against each other.
_STALE_HANDLE_RE = re.compile(
    r"Timeout .*exceeded|not visible|not attached|detached|Target closed",
    re.IGNORECASE,
)


@dataclass
class SolveResult:
    """Mirror of the TS `SolveResult`."""

    is_solved: bool
    final_mouse_position: Tuple[float, float]
    token_usage: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class _GridSession:
    """Cached geometry for one reCAPTCHA grid puzzle. Mirror of `GridSession`."""

    grid_boxes: List[Sequence[int]]
    element_box: Dict[str, float]
    scale_x: float
    scale_y: float
    screenshot_w: int
    screenshot_h: int


@dataclass
class PageSolverConfig:
    """
    Tunables, named to match the TS `CaptchaKrakenConfig` keys (snake_cased) so a
    value tuned on one driver can be found on the other.
    """

    max_solve_loops: int = 10
    overall_solve_timeout_ms: int = 120_000
    post_solve_delay_ms: int = 1_200
    post_solve_outcome_timeout_ms: int = 2_500
    element_screenshot_timeout_ms: int = 8_000
    max_unsupported_resolves: int = 3
    max_stale_element_retries: int = 3
    stale_element_backoff_ms: int = 900

    # Freshness guard — re-solve if the frame moved during inference.
    stale_frame_resolve_enabled: bool = True
    stale_frame_diff_threshold: float = 0.02
    max_stale_frame_resolves: int = 2

    # Pixel-settle monitor.
    settle_poll_ms: int = 220
    settle_frames: int = 2
    settle_timeout_ms: int = 9_000
    animated_challenge_after_ms: int = 4_500
    settle_diff_threshold: float = 0.01
    post_submit_change_timeout_ms: int = 4_000

    # Grid load / dynamic-refresh timing.
    grid_load_poll_interval_ms: int = 250
    grid_load_timeout_ms: int = 8_000
    recaptcha_max_dynamic_rounds: int = 8
    recaptcha_fade_onset_grace_ms: int = 4_000
    recaptcha_dynamic_fade_poll_ms: int = 250
    recaptcha_dynamic_fade_wait_ms: int = 6_000
    recaptcha_tile_hover_enabled: bool = True


class PageSolver:
    """
    Drives one Playwright-compatible `page` through a captcha.

        from playwright.sync_api import sync_playwright
        from captchakraken.page_solver import PageSolver

        solver = PageSolver()
        result = solver.solve(page)
        if result.is_solved: ...

    One instance is reusable across solves; state is reset per `solve()`.
    """

    def __init__(
        self,
        config: Optional[PageSolverConfig] = None,
        solver: Optional[CaptchaSolver] = None,
        **solver_kwargs: Any,
    ) -> None:
        self.config = config or PageSolverConfig()
        # One CaptchaSolver for the whole driver: it owns the planner, which
        # accumulates token usage and holds the HTTP session to vLLM.
        self._solver = solver or CaptchaSolver(**solver_kwargs)
        self._last_mouse: Tuple[float, float] = (0.0, 0.0)
        self._last_submit_frame_hash: Optional[str] = None

    # ------------------------------------------------------------------
    # Shared-half bridges. Each of these is the in-process equivalent of one
    # CLI subcommand the TS driver shells out for.
    # ------------------------------------------------------------------

    def _find_grid(self, image_path: str) -> Optional[List[Sequence[int]]]:
        """`captchakraken find-grid` — row-major cell boxes in screenshot pixels."""
        from .tool_calls.find_grid import find_grid

        try:
            return find_grid(image_path)
        except Exception as exc:  # pragma: no cover - CV failure is never fatal
            _debug(f"find_grid failed: {exc}")
            return None

    def _has_movement(self, path_a: str, path_b: str, threshold: float) -> bool:
        """`captchakraken check-movement`."""
        from .image_processor import ImageProcessor

        try:
            return bool(ImageProcessor.detect_movement(path_a, path_b, threshold))
        except Exception as exc:
            _debug(f"detect_movement failed: {exc}")
            return False

    def _grid_cell_states(
        self, path_a: str, path_b: str, grid_boxes: Sequence[Sequence[int]]
    ) -> Optional[Dict[str, List[int]]]:
        """
        `captchakraken grid-cell-states-fixed`.

        Always the FIXED variant: the dynamic refresh blanks tiles to near-white,
        which makes find_grid fail on that frame, and a self-detecting call would
        then report "no grid" — which a naive caller misreads as "nothing
        loading", i.e. solved. Passing the cached boxes keeps empty/changing/
        selected correct while tiles are blank.
        """
        from .cli import _compute_grid_cell_states

        try:
            return _compute_grid_cell_states(path_a, path_b, list(grid_boxes))
        except Exception as exc:
            _debug(f"grid_cell_states failed: {exc}")
            return None

    def _get_solution(
        self, image_path: str, puzzle_source: str, retry_mode: Optional[str]
    ) -> Tuple[List[CaptchaAction], List[Dict[str, Any]]]:
        """
        The model query. In-process where TS spawns the CLI.

        Token usage is read off the planner by DELTA rather than reset, because
        the planner is shared across the whole solve and the caller wants a
        per-round figure that still sums to the session total.
        """
        planner = self._solver.planner
        before = len(planner.token_usage)
        actions = self._solver.solve(
            image_path, puzzle_source=puzzle_source, retry_mode=retry_mode
        )
        usage = [dict(u) for u in planner.token_usage[before:]]
        if not isinstance(actions, list):
            actions = [actions]
        return actions, usage

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def _trace_path(self, page: Any, points: Sequence[Tuple[float, float]], timings: Sequence[float]) -> None:
        viewport = {"width": 1920, "height": 1080}
        try:
            vp = page.viewport_size
            if callable(vp):  # some adapters expose it as a method
                vp = vp()
            if vp:
                viewport = vp
        except Exception:
            pass

        start = time.monotonic() * 1000.0
        for i, (x, y) in enumerate(points):
            try:
                cx = max(0.0, min(float(x), float(viewport["width"])))
                cy = max(0.0, min(float(y), float(viewport["height"])))
                page.mouse.move(cx, cy)
                self._last_mouse = (cx, cy)
                if i < len(timings):
                    target = start + timings[i]
                    _delay(target - time.monotonic() * 1000.0)
            except Exception as exc:
                msg = str(exc)
                if "Target closed" in msg or "Session closed" in msg:
                    _log("could not move mouse; page or session closed")
                    return
                # Any other per-sample failure is skipped rather than fatal —
                # losing one mousemove must not lose the solve.

    def _smooth_move(self, page: Any, x: float, y: float) -> None:
        points, timings = generate_trajectory(self._last_mouse, (x, y), 60)
        self._trace_path(page, points, timings)

    def _move_to_element(self, page: Any, element: Any, padding_percentage: float = 25.0) -> None:
        try:
            element.scroll_into_view_if_needed()
        except Exception:
            pass
        box = element.bounding_box()
        if not box:
            raise CaptchaSolveError("element has no bounding box")
        pad = padding_percentage / 100.0
        pad_x, pad_y = box["width"] * pad, box["height"] * pad
        target_x = box["x"] + pad_x + random.random() * (box["width"] - 2 * pad_x)
        target_y = box["y"] + pad_y + random.random() * (box["height"] - 2 * pad_y)
        self._smooth_move(page, target_x, target_y)

    def _move_and_click(self, page: Any, element: Any) -> None:
        self._move_to_element(page, element)
        page.mouse.down()
        _delay(random.random() * 20 + 20)
        page.mouse.up()

    def _execute_click(
        self, page: Any, action: Dict[str, Any], element_box: Dict[str, float]
    ) -> None:
        bbox = action.get("target_bounding_box")
        coords = action.get("target_coordinates")
        if bbox:
            min_x, min_y, max_x, max_y = (float(v) for v in bbox)
            px_min_x = min_x * element_box["width"]
            px_max_x = max_x * element_box["width"]
            px_min_y = min_y * element_box["height"]
            px_max_y = max_y * element_box["height"]
            # 10% inset, so a click never lands on a tile's border.
            pad_x = (px_max_x - px_min_x) * 0.1
            pad_y = (px_max_y - px_min_y) * 0.1
            rel_x = (px_min_x + pad_x) + random.random() * ((px_max_x - pad_x) - (px_min_x + pad_x))
            rel_y = (px_min_y + pad_y) + random.random() * ((px_max_y - pad_y) - (px_min_y + pad_y))
        elif coords:
            rel_x = float(coords[0]) * element_box["width"]
            rel_y = float(coords[1]) * element_box["height"]
        else:
            _log("click action without coordinates or bounding box; skipping")
            return

        self._smooth_move(page, element_box["x"] + rel_x, element_box["y"] + rel_y)
        page.mouse.down()
        _delay(random.random() * 30 + 20)
        page.mouse.up()

    def _execute_drag(
        self, page: Any, action: Dict[str, Any], element_box: Dict[str, float]
    ) -> None:
        def center(bbox: Sequence[float]) -> Tuple[float, float]:
            return (
                element_box["x"] + ((float(bbox[0]) + float(bbox[2])) / 2) * element_box["width"],
                element_box["y"] + ((float(bbox[1]) + float(bbox[3])) / 2) * element_box["height"],
            )

        src = center(action["source_bounding_box"])
        dst = center(action["target_bounding_box"])
        self._smooth_move(page, *src)
        page.mouse.down()
        _delay(random.random() * 50 + 50)
        self._smooth_move(page, *dst)
        _delay(random.random() * 50 + 50)
        page.mouse.up()

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

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
                    selector, "node => (typeof node.value === 'string' ? node.value : '')"
                )
            return bool(value and str(value).strip())
        except Exception:
            return False

    def _is_recaptcha_anchor_checked(self, anchor_iframe: Any) -> bool:
        try:
            frame = anchor_iframe.content_frame()
            if not frame:
                return False
            checked = frame.query_selector(".recaptcha-checkbox-checked")
            return self._visible(checked)
        except Exception:
            return False

    def _is_hcaptcha_anchor_checked(self, anchor_iframe: Any) -> bool:
        # hCaptcha sets <div id="checkbox" aria-checked="true"> on success. Demo
        # pages don't always populate h-captcha-response, so this visual state is
        # the necessary tie-breaker rather than a nicety.
        try:
            frame = anchor_iframe.content_frame()
            if not frame:
                return False
            return self._visible(frame.query_selector('#checkbox[aria-checked="true"]'))
        except Exception:
            return False

    def has_interactive_widget_in_dom(self, page: Any) -> bool:
        """
        Broader than `detect_captcha`: is a widget in the DOM AT ALL, even if it
        has not rendered? Distinguishes "still loading, wait for it" from
        "reCAPTCHA v3 / invisible, fail fast" — two cases a null detection
        cannot tell apart.
        """
        try:
            for anchor in page.query_selector_all('iframe[src*="recaptcha/api2/anchor"]'):
                src = anchor.get_attribute("src") or ""
                # v3 / invisible-v2 injects only an anchor, with size=invisible,
                # and never a challenge frame. Excluded here so we fail fast.
                if not re.search(r"[?&]size=invisible", src):
                    return True
            if page.query_selector('iframe[src*="recaptcha/api2/bframe"]'):
                return True
            if page.query_selector('iframe[src*="hcaptcha"][src*="frame=checkbox"]'):
                return True
            if page.query_selector('iframe[src*="hcaptcha"][src*="frame=challenge"]'):
                return True
        except Exception:
            pass
        return False

    def detect_captcha(self, page: Any) -> Optional[Any]:
        """Open challenges first, then unsolved checkboxes. Mirror of `detectCaptcha`."""
        recaptcha_challenge = page.query_selector('iframe[src*="recaptcha/api2/bframe"]')
        if self._visible(recaptcha_challenge):
            return recaptcha_challenge

        # Match the URL fragment, not the title: hCaptcha's ANCHOR title also
        # says "hCaptcha security challenge", so a title match mis-classifies it.
        hcaptcha_challenge = page.query_selector('iframe[src*="hcaptcha"][src*="frame=challenge"]')
        if self._visible(hcaptcha_challenge):
            return hcaptcha_challenge

        recaptcha_checkbox = page.query_selector('iframe[src*="recaptcha/api2/anchor"]')
        if self._visible(recaptcha_checkbox) and not self._is_recaptcha_anchor_checked(
            recaptcha_checkbox
        ):
            return recaptcha_checkbox

        hcaptcha_checkbox = page.query_selector('iframe[src*="hcaptcha"][src*="frame=checkbox"]')
        if self._visible(hcaptcha_checkbox):
            has_token = self._has_non_empty_field_value(page, '[name="h-captcha-response"]')
            if not has_token and not self._is_hcaptcha_anchor_checked(hcaptcha_checkbox):
                return hcaptcha_checkbox

        turnstile_iframe = page.query_selector('iframe[src*="challenges.cloudflare.com"]')
        if self._visible(turnstile_iframe) and not self._has_non_empty_field_value(
            page, '[name="cf-turnstile-response"]'
        ):
            return turnstile_iframe

        # Closed shadow roots hide the iframe; the container is still findable.
        turnstile_container = page.query_selector(".cf-turnstile")
        if self._visible(turnstile_container) and not self._has_non_empty_field_value(
            page, '[name="cf-turnstile-response"]'
        ):
            return turnstile_container

        return None

    def is_captcha_solved(self, page: Any) -> bool:
        """
        The vendor's definitive DONE signal — anchor checked or token populated.

        Needed because after the final submit hCaptcha keeps the challenge iframe
        VISIBLE for a couple of seconds while it verifies. Treating that frame as
        a fresh puzzle burns ~18s re-running the pipeline on a closing frame.
        """
        try:
            hc = page.query_selector('iframe[src*="hcaptcha"][src*="frame=checkbox"]')
            if self._visible(hc):
                if self._has_non_empty_field_value(page, '[name="h-captcha-response"]'):
                    return True
                if self._is_hcaptcha_anchor_checked(hc):
                    return True
            rc = page.query_selector('iframe[src*="recaptcha/api2/anchor"]')
            if self._visible(rc):
                if self._has_non_empty_field_value(page, '[name="g-recaptcha-response"]'):
                    return True
                if self._is_recaptcha_anchor_checked(rc):
                    return True
        except Exception:
            pass
        return False

    def _is_challenge_freshly_rendered(self, page: Any) -> bool:
        """
        A NEXT ROUND (prompt painted) as opposed to a frame animating closed
        (prompt already gone). Lets a multi-round solve move on immediately
        instead of waiting out the full post-submit window.
        """
        try:
            hc = page.query_selector('iframe[src*="hcaptcha"][src*="frame=challenge"]')
            if self._visible(hc):
                frame = hc.content_frame()
                prompt = frame.query_selector(".prompt-text") if frame else None
                if self._visible(prompt):
                    text = prompt.text_content() or ""
                    if text.strip():
                        return True
            rc = page.query_selector('iframe[src*="recaptcha/api2/bframe"]')
            if self._visible(rc):
                frame = rc.content_frame()
                instructions = (
                    frame.query_selector(".rc-imageselect-instructions, #rc-imageselect")
                    if frame
                    else None
                )
                if self._visible(instructions):
                    return True
        except Exception:
            pass
        return False

    def _has_recaptcha_underselect_error(self, page: Any) -> bool:
        """
        reCAPTCHA's "select all matching images" banner, shown after Verify with
        an incomplete selection.

        Critically, the tiles do NOT refresh on this error: without special
        handling the model sees the same image, answers `done` (to it, everything
        matching IS selected), we click Verify again, and the solve loops until
        it times out. Detecting it switches the next call into missed-tiles mode.
        """
        try:
            bframe = page.query_selector('iframe[src*="recaptcha/api2/bframe"]')
            if not bframe:
                return False
            frame = bframe.content_frame()
            if not frame:
                return False
            for selector in (
                ".rc-imageselect-error-select-more",
                ".rc-imageselect-error-dynamic-more",
                ".rc-imageselect-incorrect-response",
            ):
                element = frame.query_selector(selector)
                if not element:
                    continue
                # reCAPTCHA toggles these via aria-hidden on a wrapper, so
                # presence + non-empty text is the reliable test.
                if self._visible(element):
                    text = element.text_content() or ""
                    if text.strip():
                        return True
        except Exception:
            pass
        return False

    def _get_verify_button(self, frame: Any) -> Optional[Any]:
        for text in ("Verify", "Next", "Submit", "Skip"):
            lowered = text.lower()
            try:
                button = frame.query_selector(
                    f"xpath=//button[contains(translate(., "
                    f"'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{lowered}')]"
                    f" | //div[@role='button' and contains(translate(., "
                    f"'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{lowered}')]"
                )
                if self._visible(button):
                    return button
            except Exception:
                pass
        try:
            recaptcha_verify = frame.query_selector("#recaptcha-verify-button")
            if self._visible(recaptcha_verify):
                return recaptcha_verify
            hcaptcha_verify = frame.query_selector(".button-submit")
            if self._visible(hcaptcha_verify):
                return hcaptcha_verify
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Frame readiness
    # ------------------------------------------------------------------

    def _screenshot(self, element: Any, path: str, timeout_ms: Optional[int] = None) -> None:
        """
        Short timeout + animations disabled, always.

        Playwright's default 30s stability wait hangs per-screenshot on a
        closing/animating challenge element; that alone made a multi-round solve
        take ~115s. Failing fast and skipping a frame is strictly better.
        """
        element.screenshot(
            path=path,
            timeout=timeout_ms if timeout_ms is not None else 2_500,
            animations="disabled",
        )

    def _element_frame_hash(self, element: Any) -> Optional[str]:
        path = _tmp_png("fh")
        try:
            self._screenshot(element, path)
            with open(path, "rb") as handle:
                return hashlib.sha1(handle.read()).hexdigest()
        except Exception:
            return None
        finally:
            _unlink(path)

    def _wait_for_element_settled(self, element: Any) -> str:
        """
        Poll until the element's pixels stop changing.

        Returns 'settled' | 'animated' | 'timeout'. This is a PIXEL settle; the
        caller pairs it with the DOM-level image wait so a static loading frame
        (a spinner on grey, below the diff threshold) isn't mistaken for painted
        tiles.
        """
        cfg = self.config
        start = time.monotonic() * 1000.0
        previous: Optional[str] = None
        still_streak = 0
        frames: List[str] = []
        try:
            while (time.monotonic() * 1000.0) - start < cfg.settle_timeout_ms:
                path = _tmp_png("settle")
                try:
                    self._screenshot(element, path)
                except Exception:
                    _unlink(path)
                    _delay(cfg.settle_poll_ms)
                    continue
                frames.append(path)
                if previous:
                    moved = self._has_movement(previous, path, cfg.settle_diff_threshold)
                    still_streak = 0 if moved else still_streak + 1
                    if len(frames) > 1:
                        _unlink(frames.pop(0))
                    if still_streak >= cfg.settle_frames:
                        return "settled"
                    # Still moving this late means it is not merely loading.
                    if moved and (time.monotonic() * 1000.0) - start >= cfg.animated_challenge_after_ms:
                        return "animated"
                previous = frames[-1]
                _delay(cfg.settle_poll_ms)
            return "timeout"
        finally:
            for path in frames:
                _unlink(path)

    def _wait_for_change_since(self, element: Any, since_hash: str) -> bool:
        """After a submit the frame MUST change (next round, or closing)."""
        cfg = self.config
        start = time.monotonic() * 1000.0
        while (time.monotonic() * 1000.0) - start < cfg.post_submit_change_timeout_ms:
            current = self._element_frame_hash(element)
            if current and current != since_hash:
                return True
            _delay(cfg.settle_poll_ms)
        return False

    def _wait_for_hcaptcha_challenge_images(self, challenge_iframe: Any) -> None:
        """
        Block until the challenge's task images have actually painted.

        hCaptcha sets each tile's background-image once the asset loads, and
        ships an empty `url("")` placeholder before it arrives. Best-effort: a
        timeout falls through to the screenshot, where the existing fail-fast
        path still covers a genuinely unsupported puzzle.
        """
        try:
            frame = challenge_iframe.content_frame()
            if not frame:
                return
            frame.wait_for_selector(".prompt-text", state="visible", timeout=8_000)
            frame.wait_for_function(
                """() => {
                    const tiles = Array.from(
                        document.querySelectorAll('.task-image .image, .task .image'));
                    if (tiles.length > 0) {
                        return tiles.every((el) => {
                            const bg = getComputedStyle(el).backgroundImage;
                            return bg && bg !== 'none' && !/url\\(["']?["']?\\)/.test(bg);
                        });
                    }
                    const canvas = document.querySelector('canvas');
                    if (canvas && canvas.width > 0 && canvas.height > 0) return true;
                    const example = document.querySelector(
                        '.challenge-example img, .image-wrapper img');
                    return !!(example && example.complete && example.naturalWidth > 0);
                }""",
                timeout=8_000,
            )
        except Exception:
            pass  # timed out or detached mid-load; screenshot anyway

    def _wait_for_grid_cells_loaded(self, element: Any) -> bool:
        """
        reCAPTCHA fades new tiles in over ~1s, on first load and on every
        in-place refresh. Screenshotting mid-fade feeds the model a partial grid.
        """
        cfg = self.config
        start = time.monotonic() * 1000.0
        frames: List[str] = []
        try:
            while (time.monotonic() * 1000.0) - start < cfg.grid_load_timeout_ms:
                path = _tmp_png("gridpoll")
                try:
                    self._screenshot(element, path)
                except Exception:
                    _unlink(path)
                    _delay(cfg.grid_load_poll_interval_ms)
                    continue
                frames.append(path)
                if len(frames) >= 2:
                    boxes = self._find_grid(frames[-1])
                    if boxes:
                        states = self._grid_cell_states(frames[-2], frames[-1], boxes)
                        if (
                            states
                            and not states["empty"]
                            and not states["changing"]
                            and states["loaded"]
                        ):
                            return True
                    _unlink(frames.pop(0))
                _delay(cfg.grid_load_poll_interval_ms)
            return False
        except Exception:
            return False
        finally:
            for path in frames:
                _unlink(path)

    def _get_grid_boxes(self, element: Any) -> Optional[Dict[str, Any]]:
        """
        Detect the grid ONCE per puzzle session. Geometry is stable across the
        in-place dynamic refresh (only tile images change), so callers cache it.
        """
        path = _tmp_png("findgrid")
        try:
            self._screenshot(element, path, timeout_ms=self.config.element_screenshot_timeout_ms)
            boxes = self._find_grid(path)
            if not boxes or len(boxes) not in (9, 16):
                return None
            dims = _read_png_dimensions(path)
            if not dims:
                return None
            return {
                "boxes": [list(b) for b in boxes],
                "size": 4 if len(boxes) == 16 else 3,
                "screenshot_w": dims[0],
                "screenshot_h": dims[1],
            }
        except Exception:
            return None
        finally:
            _unlink(path)

    # ------------------------------------------------------------------
    # Freshness guard
    # ------------------------------------------------------------------

    def _frame_changed_since(self, element: Any, prior_path: str, threshold: float) -> bool:
        probe = _tmp_png("freshcheck")
        try:
            self._screenshot(element, probe)
            return self._has_movement(prior_path, probe, threshold)
        except Exception:
            return False
        finally:
            _unlink(probe)

    def _solve_frame_freshness_guarded(
        self,
        element: Any,
        initial_shot: str,
        run_query: Callable[[str], Tuple[List[CaptchaAction], List[Dict[str, Any]]]],
    ) -> Tuple[List[CaptchaAction], List[Dict[str, Any]]]:
        """
        Never act on a stale frame.

        If the frame changed WHILE the model was generating, its answer describes
        an undeveloped frame whose tiles no longer line up — so re-screenshot and
        re-solve on the developed one. Token usage from every attempt is merged,
        because every attempt was really billed.
        """
        cfg = self.config
        owned: List[str] = []
        merged_usage: List[Dict[str, Any]] = []
        try:
            current = initial_shot
            actions, usage = run_query(current)
            merged_usage.extend(usage)
            if not cfg.stale_frame_resolve_enabled:
                return actions, merged_usage

            for attempt in range(cfg.max_stale_frame_resolves):
                if not self._frame_changed_since(element, current, cfg.stale_frame_diff_threshold):
                    break  # frame held still through inference — answer is valid
                fresh = _tmp_png("freshsolve")
                try:
                    self._screenshot(element, fresh)
                except Exception:
                    _unlink(fresh)
                    break  # can't grab a fresh frame — act on what we have
                owned.append(fresh)
                _log(
                    f"[freshness] frame changed during inference "
                    f"(re-solve {attempt + 1}/{cfg.max_stale_frame_resolves})"
                )
                current = fresh
                actions, usage = run_query(current)
                merged_usage.extend(usage)
            return actions, merged_usage
        finally:
            for path in owned:
                _unlink(path)

    # ------------------------------------------------------------------
    # reCAPTCHA 3x3 dynamic driver
    # ------------------------------------------------------------------

    def _cell_center_page(self, cell: int, session: _GridSession) -> Tuple[float, float]:
        x1, y1, x2, y2 = session.grid_boxes[cell - 1]
        return (
            session.element_box["x"] + ((x1 + x2) / 2) * session.scale_x,
            session.element_box["y"] + ((y1 + y2) / 2) * session.scale_y,
        )

    def _hover_cell(self, page: Any, session: _GridSession, cell: int) -> None:
        first = session.grid_boxes[0]
        cell_w = (first[2] - first[0]) * session.scale_x
        cell_h = (first[3] - first[1]) * session.scale_y
        cx, cy = self._cell_center_page(cell, session)
        self._smooth_move(
            page,
            cx + (random.random() - 0.5) * cell_w * 0.4,
            cy + (random.random() - 0.5) * cell_h * 0.4,
        )

    @staticmethod
    def _bbox_to_cell(
        bbox: Sequence[float], grid_boxes: Sequence[Sequence[int]], width: int, height: int
    ) -> Optional[int]:
        cx = ((float(bbox[0]) + float(bbox[2])) / 2) * width
        cy = ((float(bbox[1]) + float(bbox[3])) / 2) * height
        for index, (x1, y1, x2, y2) in enumerate(grid_boxes):
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                return index + 1  # 1-indexed, row-major, matching find_grid
        return None

    @staticmethod
    def _order_by_priority(loading: Sequence[int], priority: Sequence[int]) -> List[int]:
        remaining = set(loading)
        ordered: List[int] = []
        for cell in priority:
            if cell in remaining:
                ordered.append(cell)
                remaining.discard(cell)
        ordered.extend(sorted(remaining))
        return ordered

    def _current_loading_cells(
        self, page: Any, element: Any, session: _GridSession, priority: Sequence[int] = ()
    ) -> List[int]:
        """
        Watch for the ONSET of the refresh over a grace window.

        The blank/fade transition LAGS the click by a beat: reCAPTCHA holds a
        clicked tile selected (old image visible) for ~1-3s and only then blanks
        it. A single snapshot right after clicking therefore sees nothing and
        concludes the puzzle is finished. Hovers a clicked tile each poll so the
        cursor keeps moving during the wait.
        """
        cfg = self.config
        watch = list(priority) if priority else None
        start = time.monotonic() * 1000.0
        frames: List[str] = []
        hover_index = 0
        try:
            first = _tmp_png("loadchk")
            self._screenshot(element, first)
            frames.append(first)

            while (time.monotonic() * 1000.0) - start < cfg.recaptcha_fade_onset_grace_ms:
                iteration_start = time.monotonic() * 1000.0
                if priority:
                    try:
                        self._hover_cell(page, session, priority[hover_index % len(priority)])
                    except Exception:
                        pass
                    hover_index += 1
                # Enforce a minimum inter-frame gap: the in-process CV call is
                # near-instant, so without this the polls fire back-to-back on
                # near-identical frames and miss a slow fade.
                elapsed = (time.monotonic() * 1000.0) - iteration_start
                if elapsed < cfg.recaptcha_dynamic_fade_poll_ms:
                    _delay(cfg.recaptcha_dynamic_fade_poll_ms - elapsed)

                path = _tmp_png("loadchk")
                self._screenshot(element, path)
                frames.append(path)

                states = self._grid_cell_states(frames[-2], frames[-1], session.grid_boxes)
                in_scope = lambda c: watch is None or c in watch  # noqa: E731
                empty = [c for c in (states or {}).get("empty", []) if in_scope(c)]
                changing = [c for c in (states or {}).get("changing", []) if in_scope(c)]
                loading = sorted(set(empty) | set(changing))
                if loading:
                    return self._order_by_priority(loading, priority)
                _unlink(frames.pop(0))
            return []
        except Exception as exc:
            _debug(f"fade-onset error: {exc}")
            return []
        finally:
            for path in frames:
                _unlink(path)

    def _wait_for_any_clicked_tile_loaded(
        self, page: Any, element: Any, session: _GridSession, fading_cells: Sequence[int]
    ) -> bool:
        """Wait for at least one blank/fading cell to finish reloading."""
        if not fading_cells:
            return True
        cfg = self.config
        start = time.monotonic() * 1000.0
        frames: List[str] = []
        hover_index = 0
        try:
            while (time.monotonic() * 1000.0) - start < cfg.recaptcha_dynamic_fade_wait_ms:
                iteration_start = time.monotonic() * 1000.0
                if cfg.recaptcha_tile_hover_enabled:
                    try:
                        self._hover_cell(
                            page, session, fading_cells[hover_index % len(fading_cells)]
                        )
                    except Exception:
                        pass
                    hover_index += 1
                elapsed = (time.monotonic() * 1000.0) - iteration_start
                if elapsed < cfg.recaptcha_dynamic_fade_poll_ms:
                    _delay(cfg.recaptcha_dynamic_fade_poll_ms - elapsed)

                path = _tmp_png("fadepoll")
                self._screenshot(element, path)
                frames.append(path)

                if len(frames) >= 2:
                    states = self._grid_cell_states(frames[-2], frames[-1], session.grid_boxes)
                    if states and [c for c in fading_cells if c in states["loaded"]]:
                        return True
                    _unlink(frames.pop(0))
            return False
        except Exception as exc:
            _debug(f"wait-load error: {exc}")
            return False
        finally:
            for path in frames:
                _unlink(path)

    def _solve_recaptcha_grid(
        self,
        page: Any,
        element: Any,
        retry_mode: Optional[str],
        grid: Dict[str, Any],
        element_box: Dict[str, float],
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Multi-round driver for reCAPTCHA 3x3 dynamic puzzles. One call = one
        puzzle session.

        The shared Python half is authoritative about WHAT to do — it runs the
        blue-badge detector, filters already-selected and still-loading tiles,
        and returns `click` / `wait` / `done`. This driver owns the human-like
        WAITING that logic cannot: after a click round it hovers the just-clicked
        tiles and waits for at least one to finish reloading before re-solving,
        so we never burn a model call on a grid that is still mid-fade.

        Submits ONLY on `done` — never on a round-cap exit, which is left to the
        outer loop to re-detect and decide.
        """
        cfg = self.config
        session = _GridSession(
            grid_boxes=grid["boxes"],
            element_box=element_box,
            scale_x=element_box["width"] / grid["screenshot_w"],
            scale_y=element_box["height"] / grid["screenshot_h"],
            screenshot_w=grid["screenshot_w"],
            screenshot_h=grid["screenshot_h"],
        )

        clicked_order: List[int] = []
        performed_action = False
        should_submit = False
        all_usage: List[Dict[str, Any]] = []
        pending_retry = retry_mode

        for round_index in range(1, cfg.recaptcha_max_dynamic_rounds + 1):
            self._wait_for_grid_cells_loaded(element)
            shot = _tmp_png("recap")
            action: Optional[Dict[str, Any]] = None
            try:
                self._screenshot(element, shot, timeout_ms=cfg.element_screenshot_timeout_ms)
                retry_for_round = pending_retry
                pending_retry = None  # only round 1 carries the inbound hint
                actions, usage = self._solve_frame_freshness_guarded(
                    element,
                    shot,
                    lambda image_path: self._get_solution(image_path, "recaptcha", retry_for_round),
                )
                all_usage.extend(usage)
                action = _as_dict(actions[0]) if actions else None
            finally:
                _unlink(shot)

            if not action or action.get("action") == "done":
                _log(f"[recaptcha-grid] round {round_index}: done; submitting.")
                should_submit = True
                break

            if action.get("action") == "wait":
                _log(f"[recaptcha-grid] round {round_index}: waiting for tiles.")
                loading = self._current_loading_cells(page, element, session, clicked_order)
                self._wait_for_any_clicked_tile_loaded(page, element, session, loading)
                continue

            if action.get("action") == "click":
                bboxes = action.get("target_bounding_boxes") or (
                    [action["target_bounding_box"]] if action.get("target_bounding_box") else []
                )
                if not bboxes:
                    # Malformed click: treat as a soft wait so we never submit
                    # prematurely, and re-solve next round.
                    _log(f"[recaptcha-grid] round {round_index}: click with no bboxes; re-solving.")
                    _delay(500)
                    continue

                clicked_this_round: List[int] = []
                for bbox in bboxes:
                    cell = self._bbox_to_cell(
                        bbox, session.grid_boxes, session.screenshot_w, session.screenshot_h
                    )
                    self._execute_click(page, {"target_bounding_box": bbox}, element_box)
                    if cell is not None:
                        clicked_order.append(cell)
                        clicked_this_round.append(cell)
                    _delay(random.random() * 80 + 80)
                performed_action = True
                _log(
                    f"[recaptcha-grid] round {round_index}: clicked {len(bboxes)} tile(s) "
                    f"-> cells {clicked_this_round}."
                )

                loading = self._current_loading_cells(page, element, session, clicked_this_round)
                if not loading:
                    # Nothing faded within the grace window → the model fully
                    # solved it; submit rather than burning another round.
                    _log(f"[recaptcha-grid] round {round_index}: nothing loading; submitting.")
                    should_submit = True
                    break
                self._wait_for_any_clicked_tile_loaded(page, element, session, loading)
                continue

            _log(f"[recaptcha-grid] round {round_index}: unexpected action; re-solving.")

        if should_submit:
            frame = element.content_frame()
            if frame:
                verify = self._get_verify_button(frame)
                if verify:
                    _log("[recaptcha-grid] clicking Verify to submit.")
                    self._move_and_click(page, verify)

        return performed_action, all_usage

    # ------------------------------------------------------------------
    # One pass over a rendered challenge
    # ------------------------------------------------------------------

    def _solve_single(
        self, page: Any, element: Any, retry_mode: Optional[str]
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        src = None
        try:
            src = element.get_attribute("src")
        except Exception:
            pass
        src = src or ""

        # The vendor hint routes to the right pipeline. It matters: hCaptcha
        # click puzzles must never go through grid detection, because find_grid
        # false-positives on the header/footer bands.
        if "hcaptcha.com" in src:
            puzzle_source = "hcaptcha"
        elif "recaptcha/api2" in src:
            puzzle_source = "recaptcha"
        else:
            puzzle_source = "unknown"

        # hCaptcha REUSES the challenge iframe across rounds: after a submit it
        # briefly shows the previous round, then a spinner, then the next one.
        # Screenshotting any of those transitional frames feeds the model a
        # blank/stale grid it correctly calls "unsupported" — which used to abort
        # the whole solve on round 2.
        if puzzle_source == "hcaptcha" and "frame=challenge" in src:
            if self._last_submit_frame_hash:
                self._wait_for_change_since(element, self._last_submit_frame_hash)
                self._last_submit_frame_hash = None
            self._wait_for_hcaptcha_challenge_images(element)
            if self._wait_for_element_settled(element) == "animated":
                raise AnimatedChallengeError(
                    "the challenge never settles (likely a video/animated puzzle)"
                )

        # Only the image-challenge frame holds a grid. Running grid detection on
        # the anchor checkbox just burns an 8s timeout before the click.
        is_recaptcha_challenge = puzzle_source == "recaptcha" and "recaptcha/api2/bframe" in src
        is_recaptcha_one_shot = False
        if is_recaptcha_challenge:
            self._wait_for_grid_cells_loaded(element)
            grid = self._get_grid_boxes(element)
            if grid and grid["size"] == 3:
                # 3x3 refreshes tiles in place, so it needs the multi-round
                # driver. 4x4 only ever returns `checked` and is one-shot.
                element_box = element.bounding_box()
                if element_box:
                    return self._solve_recaptcha_grid(
                        page, element, retry_mode, grid, element_box
                    )
            elif grid and grid["size"] == 4:
                is_recaptcha_one_shot = True

        shot = _tmp_png("captcha")
        performed_action = False
        all_usage: List[Dict[str, Any]] = []
        try:
            self._screenshot(element, shot, timeout_ms=self.config.element_screenshot_timeout_ms)

            actions, all_usage = self._solve_frame_freshness_guarded(
                element,
                shot,
                lambda image_path: self._get_solution(image_path, puzzle_source, retry_mode),
            )

            element_box = element.bounding_box()
            if not element_box:
                raise CaptchaSolveError("could not get bounding box of captcha element")

            _log(f"executing {len(actions)} action(s)")
            frame = element.content_frame()
            verify_button = None

            for raw_action in actions:
                action = _as_dict(raw_action)
                kind = action.get("action")
                if kind == "click":
                    bboxes = action.get("target_bounding_boxes") or (
                        [action["target_bounding_box"]]
                        if action.get("target_bounding_box")
                        else []
                    )
                    if not bboxes and not action.get("target_coordinates"):
                        _log("click action has no bboxes or coordinates; skipping")
                        continue
                    if bboxes:
                        for bbox in bboxes:
                            self._execute_click(page, {"target_bounding_box": bbox}, element_box)
                            _delay(random.random() * 80 + 80)
                    else:
                        self._execute_click(page, action, element_box)
                    performed_action = True
                elif kind == "drag":
                    self._execute_drag(page, action, element_box)
                    performed_action = True
                elif kind == "wait":
                    duration = int(action.get("duration_ms") or 0)
                    if duration > 0:
                        _delay(duration)
                        performed_action = True

                if frame:
                    verify_button = self._get_verify_button(frame)
                    if verify_button:
                        self._move_to_element(page, verify_button)

            # Submit policy:
            #   hCaptcha        — every puzzle is one-shot; Verify submits it.
            #   reCAPTCHA 4x4   — one-shot too (never fades), so submit now.
            #   no action/done  — submit to advance.
            # (reCAPTCHA 3x3 never reaches here; it returned above.)
            should_submit = (
                not performed_action or puzzle_source == "hcaptcha" or is_recaptcha_one_shot
            )
            if should_submit and frame and verify_button:
                _log(f"clicking Verify to submit ({puzzle_source}).")
                self._move_and_click(page, verify_button)
                # Snapshot at submit time so the NEXT attempt waits for the real
                # transition before treating whatever is on screen as fresh.
                self._last_submit_frame_hash = self._element_frame_hash(element)
        finally:
            _unlink(shot)

        return performed_action, all_usage

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def solve(self, page: Any) -> SolveResult:
        """
        Solve whatever captcha is on `page`.

        Returns a `SolveResult`; raises `NoCaptchaFoundError`,
        `UnsupportedChallengeError`, `AnimatedChallengeError`, or
        `CaptchaSolveError` on the failure modes named in each type.
        """
        cfg = self.config
        start = time.monotonic() * 1000.0
        cumulative_usage: List[Dict[str, Any]] = []
        self._last_submit_frame_hash = None

        # Mint one session id for the WHOLE solve, exactly as the TS driver does
        # per `solve()`. The planner turns it into `X-CK-Session`, which is what
        # lets the hosted gateway group this captcha's 1..N inference rounds into
        # a single billable attempt. Without it a multi-round dynamic 3x3 bills
        # as several attempts instead of one, so this is a billing-correctness
        # requirement, not telemetry.
        #
        # Restored rather than left set: a stale id leaking into the NEXT solve
        # would merge two separate captchas into one attempt — the opposite
        # error, and the one that under-bills.
        previous_session = os.environ.get(_SESSION_ENV)
        os.environ[_SESSION_ENV] = str(uuid.uuid4())
        try:
            return self._solve_impl(page, start, cumulative_usage)
        finally:
            if previous_session is None:
                os.environ.pop(_SESSION_ENV, None)
            else:
                os.environ[_SESSION_ENV] = previous_session

    def _solve_impl(
        self,
        page: Any,
        start: float,
        cumulative_usage: List[Dict[str, Any]],
    ) -> SolveResult:
        cfg = self.config

        pending_retry_mode: Optional[str] = None
        already_retried_underselect = False
        unsupported_retries = 0
        stale_element_retries = 0
        has_interacted = False
        render_waits = 0
        max_render_waits = 6

        for attempt in range(1, cfg.max_solve_loops + 1):
            if (time.monotonic() * 1000.0) - start > cfg.overall_solve_timeout_ms:
                raise CaptchaSolveError(
                    f"captcha solve timed out after {cfg.overall_solve_timeout_ms}ms "
                    f"(attempt {attempt}/{cfg.max_solve_loops})"
                )

            element = self.detect_captcha(page)
            if not element:
                # Two-stage. A null detection splits into two very different
                # cases and treating them alike is how you either hang on a
                # v3 page or give up on a slow-rendering widget.
                if has_interacted:
                    _log("no supported captcha remains after interaction; considering solved.")
                    return SolveResult(True, self._last_mouse, _aggregate(cumulative_usage))
                if self.has_interactive_widget_in_dom(page) and render_waits < max_render_waits:
                    render_waits += 1
                    _log(
                        f"widget in DOM but not yet rendered; waiting "
                        f"({render_waits}/{max_render_waits})."
                    )
                    _delay(800 + random.random() * 300)
                    continue
                raise NoCaptchaFoundError(
                    "no interactive captcha widget detected (likely reCAPTCHA v3 / "
                    "invisible or a click-triggered challenge)"
                )

            _log(f"--- captcha solve loop {attempt}/{cfg.max_solve_loops} ---")
            retry_mode_this_loop = pending_retry_mode
            pending_retry_mode = None

            try:
                did_interact, usage = self._solve_single(page, element, retry_mode_this_loop)
            except AnimatedChallengeError:
                raise
            except UnsupportedCaptchaError:
                # A settled frame the model cannot solve is normally definitive.
                # BUT mid-solve, a transitional blank frame produces the same
                # verdict — that was the "solves round 1, dies on round 2" bug.
                if has_interacted and unsupported_retries < cfg.max_unsupported_resolves:
                    unsupported_retries += 1
                    current = self.detect_captcha(page)
                    if current and self._wait_for_element_settled(current) == "animated":
                        raise AnimatedChallengeError(
                            "the challenge never settles (likely a video puzzle)"
                        )
                    _log(
                        f'"unsupported" mid-solve; settled and retrying '
                        f"({unsupported_retries}/{cfg.max_unsupported_resolves})."
                    )
                    continue
                raise UnsupportedChallengeError(
                    "cannot solve this kind of captcha — the rendered puzzle is not a "
                    "supported grid or checkbox (likely an hCaptcha click/drag puzzle)"
                )
            except Exception as exc:
                # A stale/detached handle after a submit is a TRANSITION, not a
                # dead puzzle: hCaptcha swapped in the next round while we held
                # the old iframe. Only after interacting — a first-frame failure
                # is a genuine problem worth surfacing.
                message = str(exc)
                if (
                    has_interacted
                    and stale_element_retries < cfg.max_stale_element_retries
                    and _STALE_HANDLE_RE.search(message)
                ):
                    stale_element_retries += 1
                    _log(
                        f"stale challenge handle after submit; re-detecting next round "
                        f"({stale_element_retries}/{cfg.max_stale_element_retries})."
                    )
                    _delay(cfg.stale_element_backoff_ms)
                    continue
                raise

            has_interacted = has_interacted or did_interact
            render_waits = 0
            cumulative_usage.extend(usage)

            if did_interact:
                # Poll for the vendor's SOLVED signal before re-entering the
                # pipeline. hCaptcha keeps the challenge visible for a couple of
                # seconds while verifying; without this the loop re-solves that
                # closing frame and burns ~18s. Only ever early-RETURNS on a
                # definitive signal, so it cannot loop.
                deadline = time.monotonic() * 1000.0 + cfg.post_solve_outcome_timeout_ms
                solved = False
                while time.monotonic() * 1000.0 < deadline:
                    if self.is_captcha_solved(page):
                        solved = True
                        break
                    if self._is_challenge_freshly_rendered(page):
                        break  # next round is up; go solve it now
                    _delay(200)
                if solved:
                    return SolveResult(True, self._last_mouse, _aggregate(cumulative_usage))
            else:
                _delay(cfg.post_solve_delay_ms + random.random() * 300)

            if self._has_recaptcha_underselect_error(page):
                if already_retried_underselect:
                    raise CaptchaSolveError(
                        "reCAPTCHA still showing the under-selection error after retry; "
                        "aborting (model unable to identify the missed tile)"
                    )
                _log("reCAPTCHA under-selection error; retrying with missed-tiles prompt.")
                pending_retry_mode = "missed-tiles"
                already_retried_underselect = True

            if not self.detect_captcha(page):
                return SolveResult(True, self._last_mouse, _aggregate(cumulative_usage))

            if not did_interact:
                raise CaptchaSolveError(
                    "captcha still detected but the solver performed no interactions; "
                    "aborting to avoid an infinite loop"
                )

        raise CaptchaSolveError(
            f"captcha still detected after {cfg.max_solve_loops} solve loops"
        )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _as_dict(action: Any) -> Dict[str, Any]:
    """Actions arrive as pydantic models in-process, or dicts from JSON."""
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
    """Mirror of `aggregateTokenUsage`: one summed row, plus the raw rounds."""
    if not usage:
        return []
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for entry in usage:
        for key in total:
            total[key] += int(entry.get(key) or 0)
    return [{"rounds": len(usage), **total}]


def _read_png_dimensions(path: str) -> Optional[Tuple[int, int]]:
    """
    Width/height from the IHDR chunk, so no image-size dependency is needed.
    PNG signature is 8 bytes, IHDR length+type another 8, then two big-endian
    uint32s at offsets 16 and 20.
    """
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
        if len(header) < 24 or header[1:4] != b"PNG":
            return None
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        return (width, height) if width and height else None
    except OSError:
        return None


def solve_captcha_on_page(page: Any, **kwargs: Any) -> SolveResult:
    """
    One-shot convenience wrapper mirroring the TS `new CaptchaKrakenSolver().solve(page)`.

    Synchronous only. The async Playwright API needs a parallel implementation
    (`await` at every call site) rather than a wrapper — sync Playwright handles
    cannot be driven from inside an event loop, so `asyncio.to_thread` would not
    save this. Async support is not yet written.
    """
    return PageSolver(**kwargs).solve(page)
