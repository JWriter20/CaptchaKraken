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
import os
import random
import re
import shutil
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
    # flush=True is load-bearing, not tidiness. A solve is a minutes-long
    # sequence of slow steps, and Python block-buffers stdout whenever it is not
    # a TTY — so piped or redirected (CI logs, `> run.log`, a supervisor) the
    # progress lines all appear at once when the process exits. A run that is
    # working then looks indistinguishable from one that is hung, which is
    # exactly the wrong signal from the one output people watch.
    print(f"[captchakraken] {message}", flush=True)


def _debug(message: str) -> None:
    if DEBUG:
        print(f"[captchakraken:debug] {message}", flush=True)


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
    """An animated challenge we could not RECORD.

    Note the narrowed meaning. "The challenge never stops moving" is no longer a
    failure: `video_solve_enabled` records the widget, slices the recording into
    keyframes and solves those. This is raised only when that path cannot get an
    artifact to work with — the element refuses to screenshot, or the recording
    decodes to nothing.
    """


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

    # ── Animated challenges ────────────────────────────────────────────────
    # A challenge that never settles is RECORDED and solved from keyframes rather
    # than abandoned. Off switch for callers who would rather fail fast than spend
    # the recording time.
    video_solve_enabled: bool = True
    # Burst geometry. Deliberately identical to the collector's
    # (`_collect_common.BURST_DURATION_MS` / `BURST_FPS` in the finetune repo), so a
    # challenge recorded here is the same shape of artifact the model trained on —
    # same clip length, same frame rate, therefore the same keyframe slicing.
    video_burst_duration_ms: int = 4_000
    video_burst_fps: int = 10
    # How long to wait for the widget to return to the keyframe the model chose,
    # before clicking anyway. Bounded because the alternative is worse: these
    # puzzles cycle, so the state DOES come back — but if the recording caught a
    # one-off transition it never will, and a click on the model's coordinates is
    # still a better use of the remaining budget than a timeout.
    keyframe_wait_timeout_ms: int = 6_000
    keyframe_wait_poll_ms: int = 120

    # Grid load / dynamic-refresh timing.
    grid_load_poll_interval_ms: int = 250
    grid_load_timeout_ms: int = 8_000
    recaptcha_max_dynamic_rounds: int = 8
    recaptcha_fade_onset_grace_ms: int = 4_000
    recaptcha_dynamic_fade_poll_ms: int = 250
    recaptcha_dynamic_fade_wait_ms: int = 6_000
    recaptcha_tile_hover_enabled: bool = True

    # ── puzzle-piece sliders (see _execute_slide) ──────────────────────────
    # How far to nudge the handle, in px, to learn the piece's width and how
    # fast it follows. Two probes because two unknowns; far enough apart that
    # the difference between the two widths is signal rather than rounding, and
    # both small enough to stay on the shortest track observed (~250px).
    slide_probe_offsets_px: Tuple[float, ...] = (24.0, 64.0)
    # Stop steering once the piece is this close. Tighter than any vendor's
    # accept window, so the limit on solving is the model's slot estimate.
    slide_tolerance_px: float = 2.0
    # Corrections after the probes. Each costs a screenshot with the mouse held
    # down; two is enough for a linear system, and the third would only be
    # chasing a measurement that is not going to converge.
    slide_max_corrections: int = 2


# Vendors with no checkbox/challenge split — one container, one interactive
# surface. Checked in detect_captcha() after the five hard-coded reCAPTCHA /
# hCaptcha / Turnstile checks above, so those keep first refusal. Selectors
# lifted from src/captchaCollection/sources.py, which already drives these 8
# vendors nightly in the collector. Mirror of VENDOR_WIDGET_LOCATORS in
# solver.ts — keep both in the same order with the same selectors.
VENDOR_WIDGET_LOCATORS = [
    {"puzzle_source": "geetest", "selectors": [".geetest_box", ".geetest_panel_box", ".geetest_popup_window", ".geetest_widget"]},
    {"puzzle_source": "tencent", "selectors": ['iframe#tcaptcha_iframe_dy', 'iframe[id*="tcaptcha"]', 'iframe[src*="captcha.gtimg.com"]', 'iframe[src*="captcha.qq.com"]']},
    {"puzzle_source": "yidun", "selectors": [".yidun_panel", ".yidun"]},
    {"puzzle_source": "yandex", "selectors": [".CheckboxCaptcha"]},
    {"puzzle_source": "lemin", "selectors": ["#lemin-cropped-captcha", ".lemin-captcha-popup"]},
    {"puzzle_source": "prosopo", "selectors": [".prosopo-modalInner", ".procaptcha-checkbox"]},
    {"puzzle_source": "mtcaptcha", "selectors": [".mtcap"]},
    {"puzzle_source": "botdetect", "selectors": [".BDC_CaptchaDiv"]},
]


# ── where the answer goes, when it is not a click ───────────────────────────
#
# Both tables are ordered VENDOR-FIRST, GENERIC-LAST, and the driver takes the
# first visible match. That order is the whole design: a named vendor selector
# is unambiguous, while the generic patterns are guesses that happen to be right
# most of the time. Trying the guess first would, on a page that hosts a captcha
# *and* a login form, type the captcha's answer into the username box.
#
# The generic tail is not a nicety either — it is what actually fires on most
# pages. Vendors rename these classes without notice (they are anti-bot
# surfaces, so churn is the point), and our own Tier 3 fixtures render neither
# vendor's DOM. Anything that only worked via the vendor list would be a feature
# that passes review and fails in the field.

# A distorted-text captcha's answer box. The three vendor entries are the three
# types in instructions.py::TEXT_TYPES.
TEXT_INPUT_SELECTORS = [
    # BotDetect — the input is application-defined, so match the id fragment its
    # own docs and samples use. These three are what the nightly collector
    # already drives (src/captchaCollection/sources.py).
    "input[id*=captchaCode]",
    "input#captchaCode",
    "input[id*=validateCaptcha]",
    ".BDC_CaptchaDiv input[type=text]",
    # MTCaptcha
    "input.mtcap-inputtext",
    ".mtcap input[type=text]",
    # Yandex SmartCaptcha
    ".AdvancedCaptcha-Input input",
    "input.Textinput-Control",
    'input[name="rep"]',
    # Generic — an input the page itself labels as the captcha answer.
    'input[name*="captcha" i]',
    'input[id*="captcha" i]',
    'input[aria-label*="captcha" i]',
    'input[placeholder*="code" i]',
    'input[autocomplete="off"][type=text]',
    # Last resort: the only text box in the widget. Scoped to the challenge
    # frame/container by the caller, never to the whole page — see _find_control.
    "input[type=text]",
    "input:not([type])",
    "input[type=tel]",
    "textarea",
]

# The handle you drag on a puzzle-piece slider. NOT the piece: on every one of
# these vendors the piece is inert decoration that the handle carries, so a
# drag starting on the piece moves nothing at all.
SLIDER_HANDLE_SELECTORS = [
    # GeeTest v3 / v4
    ".geetest_slider_button",
    ".geetest_btn",
    ".geetest_slider .geetest_arrow",
    # Tencent
    "#tcaptcha_drag_thumb",
    ".tc-slider-normal",
    "[id*=slideBlock]",
    # Yidun (NetEase)
    ".yidun_slider",
    ".yidun_jigsaw",
    # Lemin
    ".lemin-slider-handle",
    "#lemin-cropped-captcha .slider",
    # Generic — an ARIA slider, or a class that says handle/thumb/button on a
    # track. `[draggable=true]` is deliberately absent: it is the HTML5
    # drag-and-drop opt-in, which fires dragstart rather than pointermove, and
    # no slider captcha uses it.
    '[role="slider"]',
    "[aria-valuenow]",
    '[class*="slider"][class*="btn"]',
    '[class*="slider"][class*="button"]',
    '[class*="slide"][class*="handle"]',
    '[class*="drag"][class*="thumb"]',
]

# Fallback for the sliderless members of the family. Lemin's "cropped" puzzle
# has no track at all — you drag the piece itself onto the gap — and the model
# answers it with the same sourceless drag, because from the picture the two are
# indistinguishable. Tried only after SLIDER_HANDLE_SELECTORS finds nothing.
DRAGGABLE_PIECE_SELECTORS = [
    ".lemin-cropped-puzzle-piece",
    "#lemin-cropped-captcha canvas + canvas",
    '[class*="puzzle"][class*="piece"]',
    '[class*="jigsaw"]',
]


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
        # See _seed_cursor: the (0, 0) origin wedges camoufox's humanised
        # mouse, so the first move of each solve must step off it plainly.
        self._cursor_seeded = False
        self._last_submit_frame_hash: Optional[str] = None
        # Absolute deadline for the current solve, in the same clock as
        # time.monotonic() * 1000. None outside a solve.
        self._deadline_ms: Optional[float] = None
        # Window size for clamping, resolved once per solve. See _viewport.
        self._viewport_cache: Optional[Dict[str, float]] = None

    def _check_deadline(self, where: str) -> None:
        """
        Enforce `overall_solve_timeout_ms` from INSIDE the long-running loops.

        The TypeScript driver checks its budget only at the top of each attempt,
        which means the budget is not really a budget: one slow attempt overruns
        it without bound, because nothing looks at the clock again until the
        attempt returns. Observed in practice — a camoufox session ran past ten
        minutes against a nominal 120 s timeout, and the check at the top of the
        loop never got a turn to fire.

        Called at the points that can legitimately spin for a long time: each
        action executed, and each round of the dynamic grid driver.
        """
        if self._deadline_ms is None:
            return
        if time.monotonic() * 1000.0 > self._deadline_ms:
            raise CaptchaSolveError(
                f"captcha solve exceeded overall_solve_timeout_ms "
                f"({self.config.overall_solve_timeout_ms}ms) during {where}"
            )

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
        self, image_path: str, puzzle_source: str, retry_mode: Optional[str],
        text_mode: bool = False,
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
            image_path, puzzle_source=puzzle_source, retry_mode=retry_mode,
            text_mode=text_mode,
        )
        usage = [dict(u) for u in planner.token_usage[before:]]
        if not isinstance(actions, list):
            actions = [actions]
        return actions, usage

    def _get_keyframe_solution(
        self, keyframe_paths: Sequence[str]
    ) -> Tuple[List[CaptchaAction], List[Dict[str, Any]]]:
        """The model query for an animated challenge. Usage read by DELTA, as above.

        One request for the whole keyframe set, not one per frame: the model has to
        compare the frames to find what differs between them, which it can only do
        with all of them in a single context. Per-frame queries would also cost N
        billable rounds for one puzzle.
        """
        planner = self._solver.planner
        before = len(planner.token_usage)
        actions = self._solver.solve_keyframes(keyframe_paths)
        usage = [dict(u) for u in planner.token_usage[before:]]
        if not isinstance(actions, list):
            actions = [actions]
        return actions, usage

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def _viewport(self, page: Any) -> Optional[Dict[str, float]]:
        """
        The window we must keep the cursor inside, cached per solve.

        MUST NOT be skipped and MUST NOT be guessed, because a mouse move to a
        coordinate outside the window WEDGES camoufox. Its juggler humanises
        each move into a trajectory, guards the intermediate points against the
        bounds, and then dispatches the requested destination unguarded
        ("Always finish exactly on the requested destination"). An out-of-window
        destination fires as an exit event instead of eMouseMove, so no
        hit-renderer ack comes back; dispatch is serialised on a process-global
        activation chain, so that one missing ack hangs every later input event
        forever. Symptom: `page.mouse.move()` never returns, 0% CPU, solve
        appears dead. Same failure family as camoufox #225.

        `page.viewport_size` is None under camoufox (it uses the real window
        rather than a spoofed viewport), which is why this falls back to asking
        the page itself instead of assuming a size.
        """
        if self._viewport_cache is not None:
            return self._viewport_cache
        try:
            vp = page.viewport_size
            if callable(vp):  # some adapters expose it as a method
                vp = vp()
            if vp and vp.get("width") and vp.get("height"):
                self._viewport_cache = {"width": float(vp["width"]), "height": float(vp["height"])}
                return self._viewport_cache
        except Exception:
            pass
        try:
            inner = page.evaluate("() => ({width: window.innerWidth, height: window.innerHeight})")
            if inner and inner.get("width") and inner.get("height"):
                self._viewport_cache = {
                    "width": float(inner["width"]),
                    "height": float(inner["height"]),
                }
                return self._viewport_cache
        except Exception:
            pass
        return None

    def _trace_path(self, page: Any, points: Sequence[Tuple[float, float]], timings: Sequence[float]) -> None:
        # Clamp ONLY when the viewport is actually known.
        #
        # camoufox reports `viewport_size is None` (it uses the real window
        # rather than a spoofed viewport). The obvious fallback — assume
        # 1920x1080 — is actively harmful: it clamps coordinates to the edge of
        # a viewport that is not the real one, and a coordinate sitting EXACTLY
        # on the boundary is the input that deadlocks camoufox's humanised-mouse
        # juggler patch (upstream #225, "humanize edge deadlock"). The symptom is
        # a `page.mouse.move()` that never returns: the process sits at 0% CPU
        # with no in-flight work and the solve appears hung, which is precisely
        # what this driver did against camoufox until this was found.
        #
        # A guessed clamp buys nothing anyway — the points come from a
        # trajectory between two on-screen elements, so they are already in
        # range. When we do clamp, we inset by a pixel so a legitimately
        # off-screen point lands just inside the edge instead of exactly on it.
        viewport = self._viewport(page)

        start = time.monotonic() * 1000.0
        for i, (x, y) in enumerate(points):
            try:
                if viewport is None:
                    cx, cy = float(x), float(y)
                else:
                    cx = max(1.0, min(float(x), float(viewport["width"]) - 1.0))
                    cy = max(1.0, min(float(y), float(viewport["height"]) - 1.0))
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

    def _seed_cursor(self, page: Any) -> None:
        """Move the cursor off its (0, 0) origin once per solve, in ONE plain
        move, before any humanised trajectory runs.

        Without this, the first trajectory of a solve begins at (0, 0) — the
        exact window corner, because that is where the pointer starts and
        nothing has moved it. Under camoufox's `humanize` juggler a short move
        from that origin never returns: `page.mouse.move()` blocks forever at 0%
        CPU, and since dispatch is serialised on a process-global activation
        chain, every later input event blocks behind it too. The solve looks
        hung with no error anywhere and the Tier 3 fixture run times out on all
        three attempts.

        Reduced to four lines against camoufox directly:

            with Camoufox(headless=True, humanize=True) as b:
                p = b.new_page(); p.goto("about:blank")
                p.mouse.move(1.0, 1.0)      # never returns

        The same move after ANY interior move completes in ~1.1s, which is what
        makes this the fix rather than a workaround: it is the ORIGIN that is
        poisoned, not the destination. Same failure family as camoufox #225.

        Deliberately not routed through `_smooth_move`: that would generate a
        trajectory from (0, 0) and reintroduce exactly the move being avoided.
        """
        if self._cursor_seeded:
            return
        self._cursor_seeded = True
        vp = self._viewport(page)
        # Centre when the window is known, else a modest interior point — any
        # coordinate comfortably off the corner will do.
        cx, cy = (vp["width"] / 2, vp["height"] / 2) if vp else (200.0, 200.0)
        try:
            page.mouse.move(cx, cy)
            self._last_mouse = (cx, cy)
        except Exception:  # noqa: BLE001 — an adapter without a mouse must not fail the solve
            pass

    def _smooth_move(self, page: Any, x: float, y: float) -> None:
        self._seed_cursor(page)
        points, timings = generate_trajectory(self._last_mouse, (x, y), 60)
        self._trace_path(page, points, timings)

    def _move_to_element(self, page: Any, element: Any, padding_percentage: float = 25.0) -> None:
        # BOUNDED. Playwright's default timeout is 30s, and this is called once
        # per action plus once per submit — on a challenge iframe that is
        # mid-animation, scrolling "waits for stability" and burns the full 30s
        # every time, which turned a ~5s solve loop into minutes during live
        # testing. The element is already on screen in every real case here (we
        # just screenshotted it), so a short bound loses nothing: on timeout we
        # move to wherever it currently is.
        try:
            element.scroll_into_view_if_needed(timeout=2_000)
        except TypeError:
            # An adapter whose signature takes no timeout (e.g. the Puppeteer
            # bridge). Fall back rather than fail the solve.
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
    # Typing and sliding
    # ------------------------------------------------------------------

    def _find_control(self, scope: Any, selectors: Sequence[str]) -> Optional[Any]:
        """First VISIBLE match for `selectors`, tried in order.

        `scope` is the challenge frame, or — for the vendors that render into
        the host page rather than an iframe — the widget element itself. Never
        the page: the generic tail of both selector tables would otherwise
        happily match a login form's text box or a carousel's drag handle
        somewhere else on the document, and the answer would go there.
        """
        for selector in selectors:
            try:
                element = scope.query_selector(selector)
            except Exception:
                continue  # a selector this adapter can't parse must not end the search
            if self._visible(element):
                return element
        return None

    def _execute_type(self, page: Any, scope: Any, action: Dict[str, Any]) -> bool:
        """Put the model's reading of a distorted-text captcha into its box."""
        text = str(action.get("text") or "")
        if not text:
            return False
        field = self._find_control(scope, TEXT_INPUT_SELECTORS)
        if field is None:
            _log("type action, but no text box in the widget; skipping")
            return False

        self._move_and_click(page, field)  # travel there, then press to focus
        # A retry round arrives with the previous attempt still in the box, and
        # typing would APPEND to it — submitting a string the model never read.
        try:
            page.keyboard.press("Control+A")
        except Exception:
            pass
        # Per character rather than one `type(text, delay=…)` call: a constant
        # inter-key delay is itself a signal, and these are the vendors that
        # score typing cadence.
        for ch in text:
            try:
                page.keyboard.type(ch)
            except Exception as exc:
                _log(f"could not type into the captcha field: {exc}")
                return False
            _delay(random.random() * 90 + 45)
        _log(f"typed {len(text)} character(s) into the captcha field")
        return True

    def _track_piece(
        self, element: Any, before: str, after: str, exclude: Sequence[float]
    ) -> Optional[Sequence[int]]:
        """`captchakraken track-piece` — box of what moved, handle masked out."""
        from .tool_calls.track_piece import changed_bbox

        try:
            self._screenshot(element, after, timeout_ms=self.config.element_screenshot_timeout_ms)
            return changed_bbox(before, after, exclude)
        except Exception as exc:
            _debug(f"track_piece failed: {exc}")
            return None

    def _execute_slide(
        self,
        page: Any,
        element: Any,
        scope: Any,
        action: Dict[str, Any],
        element_box: Dict[str, float],
    ) -> bool:
        """Drive a puzzle-piece slider until the PIECE reaches the model's slot.

        The model is asked for one thing here — the centre of the gap — because
        it is the only thing the picture can tell it. What it cannot know is how
        far the handle must travel to put the piece there: the handle is
        elsewhere on the widget, and the ratio between the two is a vendor
        implementation detail that several of them deliberately vary.

        So this is closed-loop, not a calculation. Press the handle, nudge it
        twice by known amounts, and watch the screen:

            union(before, after) spans the piece's ORIGINAL left edge to its
            CURRENT right edge, so its width is  piece_width + ratio × nudge.

        Two nudges, two widths, two unknowns — solve for both, then steer the
        remaining distance and re-measure. The mouse is not released until the
        piece is home, because on every one of these puzzles releasing IS the
        submit; there is no Verify button to reconsider at.

        Returns False if there is nothing here to drag, leaving the caller's
        normal no-op handling to deal with it.
        """
        target_x = (
            (float(action["target_bounding_box"][0]) + float(action["target_bounding_box"][2])) / 2
        ) * element_box["width"]

        handle = self._find_control(scope, SLIDER_HANDLE_SELECTORS)
        if handle is None:
            # No track — the sliderless members of the family (Lemin's
            # "cropped") want the piece dragged directly. Same answer from the
            # model, because the two look identical; different gesture. Nothing
            # to close a loop on, since the piece is under the cursor and moves
            # with it one for one.
            piece = self._find_control(scope, DRAGGABLE_PIECE_SELECTORS)
            if piece is None:
                _log("slide action, but the widget has neither a slider nor a draggable piece")
                return False
            box = piece.bounding_box()
            if not box:
                return False
            _log("no slider track; dragging the piece to the slot directly")
            self._smooth_move(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.down()
            _delay(random.random() * 50 + 50)
            self._smooth_move(page, element_box["x"] + target_x, box["y"] + box["height"] / 2)
            _delay(random.random() * 50 + 50)
            page.mouse.up()
            return True

        hbox = handle.bounding_box()
        if not hbox:
            return False
        start_x = hbox["x"] + hbox["width"] / 2
        hold_y = hbox["y"] + hbox["height"] / 2

        # Mask the whole horizontal BAND the handle runs in, not just where it
        # is now: it is about to move across that band, and most vendors fill
        # the track behind it as it goes. Either would otherwise be the largest
        # moving thing in frame, and we would track the handle instead of the
        # piece.
        pad = max(4.0, hbox["height"] * 0.35)
        exclude = [
            0.0,
            hbox["y"] - element_box["y"] - pad,
            element_box["width"],
            hbox["y"] + hbox["height"] - element_box["y"] + pad,
        ]

        shots = [_tmp_png("slide") for _ in range(4)]
        try:
            self._move_to_element(page, handle, padding_percentage=30.0)
            page.mouse.down()
            _delay(random.random() * 60 + 60)
            self._screenshot(element, shots[0],
                             timeout_ms=self.config.element_screenshot_timeout_ms)

            probes = self.config.slide_probe_offsets_px
            widths: List[Tuple[float, float]] = []
            last_box = None
            for offset, shot in zip(probes, shots[1:]):
                self._smooth_move(page, start_x + offset, hold_y)
                _delay(random.random() * 40 + 40)
                box = self._track_piece(element, shots[0], shot, exclude)
                if box is not None:
                    widths.append((float(offset), float(box[2] - box[0])))
                    last_box = box

            piece_w, ratio = self._solve_slide_geometry(widths, element_box["width"])
            if last_box is None or piece_w is None:
                # Never saw the piece — a canvas the screenshot cannot separate,
                # a widget that redraws wholesale, or a press the handle refused.
                # Fall back on the geometry every one of these puzzles shares:
                # piece and handle both start flush left, so the handle's travel
                # is the piece's travel.
                _log("slider: piece never resolved on screen; steering by handle travel alone")
                self._smooth_move(page, start_x + (target_x - (start_x - element_box["x"])), hold_y)
            else:
                # The offset `last_box` was MEASURED at — not `probes[-1]`, and
                # not indexed by how many measurements succeeded. If the first
                # probe failed to resolve and the second worked, those two
                # disagree, and steering from a base the reading does not belong
                # to sends the piece somewhere neither the model nor the screen
                # asked for.
                offset = float(widths[-1][0])
                for _ in range(self.config.slide_max_corrections):
                    piece_center = (last_box[2] - piece_w / 2.0)
                    error = target_x - piece_center
                    if abs(error) <= self.config.slide_tolerance_px:
                        break
                    offset += error / ratio
                    self._smooth_move(page, start_x + offset, hold_y)
                    _delay(random.random() * 40 + 40)
                    box = self._track_piece(element, shots[0], shots[3], exclude)
                    if box is None:
                        break  # ran out of track; release where we are
                    last_box = box
                _debug(f"slider: piece_w={piece_w:.1f} ratio={ratio:.3f} "
                       f"final_center={last_box[2] - piece_w / 2.0:.1f} target={target_x:.1f}")

            # Settle before letting go. A release in the same tick as the last
            # move reads as a machine, and some vendors sample the final
            # milliseconds of the gesture.
            _delay(random.random() * 120 + 90)
        finally:
            try:
                page.mouse.up()
            except Exception:
                pass
            for shot in shots:
                _unlink(shot)
        return True

    @staticmethod
    def _solve_slide_geometry(
        widths: Sequence[Tuple[float, float]], widget_width: float
    ) -> Tuple[Optional[float], float]:
        """Piece width and handle-to-piece travel ratio, from probe measurements.

        Each measurement is (handle offset, width of what changed), and
        width = piece_width + ratio × offset. Two of them determine both.

        With only one usable measurement the system is underdetermined, so ratio
        is ASSUMED to be 1 — true of every vendor observed, and the assumption
        is stated here rather than buried as a default. A ratio solved from
        implausible measurements (a redraw, a piece that hit the wall between
        probes) is rejected the same way: better a 1:1 guess that overshoots and
        gets corrected than a ratio of 0.02 that sends the handle off the track.
        """
        if not widths:
            return None, 1.0
        piece_w: Optional[float] = None
        ratio = 1.0
        if len(widths) >= 2:
            (o1, w1), (o2, w2) = widths[0], widths[-1]
            if o2 != o1:
                candidate = (w2 - w1) / (o2 - o1)
                if 0.2 <= candidate <= 3.0:
                    ratio = candidate
                    piece_w = w1 - ratio * o1
        if piece_w is None:
            o, w = widths[-1]
            piece_w = w - ratio * o
        # A piece narrower than a few pixels, or wider than half the widget, is
        # a measurement of something else.
        if not 3.0 <= piece_w <= widget_width * 0.6:
            return None, ratio
        return piece_w, ratio

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

        # Vendors with one interactive surface (no checkbox/challenge split) —
        # GeeTest, Tencent, Yidun, Yandex, Lemin, Prosopo, MTCaptcha, BotDetect.
        for entry in VENDOR_WIDGET_LOCATORS:
            for selector in entry["selectors"]:
                el = page.query_selector(selector)
                if self._visible(el):
                    return el

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
        # `.//` — RELATIVE. `scope` is an ElementHandle whenever the widget is
        # markup on the host page rather than a vendor iframe (every
        # distorted-text captcha), and a document-rooted `//button` does not
        # resolve against an element handle: the query returned None even with
        # the button sitting inside that very element, so a typed code was never
        # submitted. On a Frame the context node is the document, where `.//` and
        # `//` mean the same thing, so the vendor paths are unaffected.
        for text in ("Verify", "Next", "Submit", "Skip"):
            lowered = text.lower()
            try:
                button = frame.query_selector(
                    f"xpath=.//button[contains(translate(., "
                    f"'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{lowered}')]"
                    f" | .//div[@role='button' and contains(translate(., "
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
                self._check_deadline("waiting for the challenge to settle")
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

    # ------------------------------------------------------------------
    # Animated challenges
    # ------------------------------------------------------------------

    def _settle_or_animated(self, element: Any) -> bool:
        """Wait for the widget to settle; return whether it is animated instead.

        "Never settles" stopped being a failure. hCaptcha's "select the odd animal"
        fades its sprites on independent cycles and its "unique motion pattern"
        puzzle spins identical meshes — those challenges are animated BY DESIGN, and
        the answer only exists across frames. True here routes the caller to the
        recording path.

        `video_solve_enabled=False` restores the old behaviour for callers who would
        rather fail fast than spend the recording time.
        """
        if self._wait_for_element_settled(element) != "animated":
            return False
        if not self.config.video_solve_enabled:
            raise AnimatedChallengeError(
                "the challenge never settles and video_solve_enabled is off"
            )
        _log("[animated] challenge never settles — recording it")
        return True

    def _record_keyframes(self, element: Any) -> Tuple[List[str], str]:
        """Record the widget and return `(keyframe_paths, temp_dir)`.

        Screenshots the element at the collector's burst geometry (4 s @ 10 fps),
        then hands the frames to the SAME slicer the training data was cut with
        (`keyframes.extract_keyframes`). That shared code path is the whole point:
        the model answers with a frame NUMBER, and a number only means something if
        the live set was sliced the way the trained set was.

        Frames are kept in memory and sliced from there — no intermediate mp4. The
        old pipeline encoded one, and every clip it produced was mp4v, a codec the
        serving side may or may not decode. Skipping the encode removes that whole
        class of silent failure along with the disk round-trip.

        The caller owns `temp_dir` and must remove it once the actions are done
        with: the returned paths are read back by the wait gate on every poll, so
        cleaning up any earlier would break the click.

        Raises AnimatedChallengeError if nothing could be captured.
        """
        from .keyframes import extract_keyframes, write_keyframes

        cfg = self.config
        fps = max(1, int(cfg.video_burst_fps))
        total = max(1, round(cfg.video_burst_duration_ms / (1000.0 / fps)))
        interval = 1.0 / fps

        shot = _tmp_png("burst")
        frames = []
        try:
            for i in range(total):
                self._check_deadline("recording the animated challenge")
                start = time.monotonic()
                try:
                    self._screenshot(element, shot)
                except Exception as exc:  # noqa: BLE001 — a dropped frame is not fatal
                    _debug(f"burst frame {i} failed: {exc}")
                else:
                    import cv2

                    img = cv2.imread(shot)
                    if img is not None:
                        frames.append(img)
                # Drift-corrected: a slow screenshot must not stretch the clip, or
                # the recording covers more wall-clock than the model trained on and
                # a cycle's period lands differently across the frames.
                wait = interval - (time.monotonic() - start)
                if wait > 0 and i < total - 1:
                    time.sleep(wait)
        finally:
            _unlink(shot)

        if not frames:
            raise AnimatedChallengeError(
                "could not record the animated challenge (no frame screenshotted)"
            )
        _log(f"[animated] recorded {len(frames)} frames at {fps}fps")

        kfset = extract_keyframes(frames, fps=float(fps))
        temp_dir = tempfile.mkdtemp(prefix="ck_keyframes_")
        paths = write_keyframes(kfset, temp_dir, stem="challenge")
        _log(f"[animated] sliced to {len(paths)} keyframe(s) (mode={kfset.mode})")
        return [str(p) for p in paths], temp_dir

    def _wait_for_keyframe(self, element: Any, keyframe_path: str,
                           point_norm: Tuple[float, float]) -> bool:
        """Hold until the widget looks like `keyframe_path` around `point_norm`.

        This is the reason an animated answer names a frame. The model picked the
        moment its target was visible; the coordinates are only correct at that
        moment. Clicking as soon as the answer arrives lands on whatever the sprite
        happens to be doing — for a cross-fade, usually background.

        Compares only the neighbourhood of the action point, with the same box and
        the same metric the training label was chosen with
        (`keyframes.region_box` / `region_diff_ratio`). Local rather than
        whole-frame because everything ELSE in these puzzles is also moving: a
        whole-frame match would need every unrelated sprite to align too, and would
        essentially never open.

        Returns whether the state was reached. On timeout the caller clicks anyway
        — see `keyframe_wait_timeout_ms`.
        """
        import cv2

        from .keyframes import MATCH_REGION_TOLERANCE, region_box, region_diff_ratio

        ref = cv2.imread(keyframe_path)
        if ref is None:
            _debug(f"keyframe {keyframe_path} unreadable; not waiting")
            return False
        box = region_box(ref.shape[1::-1], point_norm)

        cfg = self.config
        deadline = (time.monotonic() * 1000.0) + cfg.keyframe_wait_timeout_ms
        probe = _tmp_png("kfwait")
        best = 1.0
        try:
            while (time.monotonic() * 1000.0) < deadline:
                self._check_deadline("waiting for the challenge keyframe")
                try:
                    self._screenshot(element, probe)
                    live = cv2.imread(probe)
                except Exception as exc:  # noqa: BLE001
                    _debug(f"keyframe probe failed: {exc}")
                    live = None
                if live is not None:
                    d = region_diff_ratio(ref, live, box)
                    best = min(best, d)
                    if d <= MATCH_REGION_TOLERANCE:
                        _log(f"[animated] widget matched the chosen keyframe (diff={d:.4f})")
                        return True
                _delay(cfg.keyframe_wait_poll_ms)
        finally:
            _unlink(probe)
        _log(f"[animated] widget never matched the chosen keyframe within "
             f"{cfg.keyframe_wait_timeout_ms}ms (closest diff={best:.4f}); "
             f"clicking on the model's coordinates anyway")
        return False

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
                self._check_deadline("waiting for grid cells to load")
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
                self._check_deadline("watching for the tile refresh")
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
                self._check_deadline("waiting for a tile to reload")
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
            # Each round can legitimately spend ~10s waiting on fades, so eight
            # of them plus the model calls can outlast the whole solve budget.
            self._check_deadline(f"recaptcha grid round {round_index}")
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

        # Everything the answer might have to be delivered INTO — a text box, a
        # slider handle — is looked up against this, never against the page.
        # For the iframed vendors it is the challenge document; for the ones
        # that render into the host page (GeeTest, Yidun, BotDetect, …) it is
        # the widget element, whose subtree is the same boundary.
        scope = element.content_frame() or element

        # Does this puzzle want a STRING rather than a place to click? Only the
        # DOM can say. The picture cannot: BotDetect's warped code and
        # hCaptcha's "click the matching character" are the same genre of image
        # and want opposite answers. Restricted to `unknown` because neither
        # hCaptcha nor reCAPTCHA has ever served a typed challenge, so a match
        # inside one of their frames would be a false positive by definition.
        text_mode = puzzle_source == "unknown" and self._find_control(
            scope, TEXT_INPUT_SELECTORS
        ) is not None
        if text_mode:
            _log("widget has a text box; solving as a distorted-text captcha")

        # hCaptcha REUSES the challenge iframe across rounds: after a submit it
        # briefly shows the previous round, then a spinner, then the next one.
        # Screenshotting any of those transitional frames feeds the model a
        # blank/stale grid it correctly calls "unsupported" — which used to abort
        # the whole solve on round 2.
        is_animated = False
        if puzzle_source == "hcaptcha" and "frame=challenge" in src:
            if self._last_submit_frame_hash:
                self._wait_for_change_since(element, self._last_submit_frame_hash)
                self._last_submit_frame_hash = None
            self._wait_for_hcaptcha_challenge_images(element)
            is_animated = self._settle_or_animated(element)
        elif puzzle_source == "unknown":
            # Non-hCaptcha, non-reCAPTCHA widgets (GeeTest, Tencent, …). The settle
            # probe was never run for these, so an animated one — GeeTest's svg board
            # cycles its glyph set — was screenshotted mid-cycle and answered from
            # whatever single moment we happened to catch. reCAPTCHA is excluded on
            # purpose: it has its own readiness gate below, its grids are never
            # animated, and a second probe would only add latency to a path that
            # already works.
            is_animated = self._settle_or_animated(element)

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
        slid = False
        placed = False
        typed = False
        all_usage: List[Dict[str, Any]] = []
        keyframe_dir: Optional[str] = None
        try:
            if is_animated:
                # The freshness guard is deliberately SKIPPED here. It re-solves when
                # the frame changes during inference, and an animated challenge
                # changes by definition — every attempt would be judged stale and the
                # whole re-solve budget would burn without ever acting. The frame
                # number in the answer is the real guard: it names the state to act
                # in, and `_execute_click` waits for it.
                keyframes, keyframe_dir = self._record_keyframes(element)
                actions, all_usage = self._get_keyframe_solution(keyframes)
            else:
                self._screenshot(element, shot, timeout_ms=self.config.element_screenshot_timeout_ms)
                actions, all_usage = self._solve_frame_freshness_guarded(
                    element,
                    shot,
                    lambda image_path: self._get_solution(
                        image_path, puzzle_source, retry_mode, text_mode=text_mode
                    ),
                )

            element_box = element.bounding_box()
            if not element_box:
                raise CaptchaSolveError("could not get bounding box of captcha element")

            _log(f"executing {len(actions)} action(s)")
            frame = element.content_frame()
            verify_button = None

            for raw_action in actions:
                self._check_deadline("action execution")
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
                    # On an animated challenge, hold each click until the widget is
                    # back in the state the model answered about. Per-click, not once
                    # per action: these puzzles keep cycling, so by the time click 2
                    # comes round the state has moved on again.
                    await_kf = action.get("await_keyframe")
                    if bboxes:
                        for bbox in bboxes:
                            if await_kf:
                                self._wait_for_keyframe(element, await_kf, _bbox_center(bbox))
                            self._execute_click(page, {"target_bounding_box": bbox}, element_box)
                            _delay(random.random() * 80 + 80)
                    else:
                        self._execute_click(page, action, element_box)
                    performed_action = True
                elif kind == "drag" and not action.get("source_bounding_box"):
                    # No source — a puzzle-piece slider. What you grab is not
                    # what has to arrive, so this cannot go through
                    # _execute_drag: pressing the gap the model named and
                    # dragging from there picks up nothing at all.
                    if self._execute_slide(page, element, scope, action, element_box):
                        performed_action = True
                        slid = True
                elif kind == "drag":
                    # Wait on the SOURCE: the piece has to be there to be picked up.
                    # The destination is not gated — by the time the mouse arrives the
                    # animation has moved on regardless, and a drop is judged by where
                    # it lands, not by what the slot looked like on pickup.
                    await_kf = action.get("await_keyframe")
                    if await_kf and action.get("source_bounding_box"):
                        self._wait_for_keyframe(
                            element, await_kf, _bbox_center(action["source_bounding_box"])
                        )
                    self._execute_drag(page, action, element_box)
                    performed_action = True
                    placed = True
                elif kind == "type":
                    if self._execute_type(page, scope, action):
                        performed_action = True
                        typed = True
                elif kind == "wait":
                    duration = int(action.get("duration_ms") or 0)
                    if duration > 0:
                        _delay(duration)
                        performed_action = True

                # `scope` when there is no vendor iframe. Eight vendors render
                # into the HOST PAGE — GeeTest, Yidun, Tencent, Yandex, Lemin,
                # Prosopo, MTCaptcha, BotDetect — so `content_frame()` is None
                # for all of them and the button was never even SEARCHED FOR,
                # while the text box and the slider handle it sits beside were
                # both found through `scope` a few lines above. Two containers
                # for two halves of one interaction.
                #
                # This used to be gated on `typed`, for fear of turning up the
                # submit of the FORM the captcha guards. `scope` is the widget
                # container and the xpaths are RELATIVE, so that button is out
                # of reach by construction; what the gate actually did was make
                # every non-typed inline puzzle unsubmittable. Measured on the
                # Tier 3 fixtures: 4 pairs aborting outright and 11 more types
                # burning all ten solve loops on a puzzle they had answered on
                # the first one. The press itself is still bounded by
                # `should_submit` below, which is where the hazard belongs.
                lookup = frame or (scope if not slid else None)
                if lookup is not None:
                    verify_button = self._get_verify_button(lookup)
                    if verify_button:
                        self._move_to_element(page, verify_button)

            # Submit policy:
            #   hCaptcha        — every puzzle is one-shot; Verify submits it.
            #   reCAPTCHA 4x4   — one-shot too (never fades), so submit now.
            #   a TYPED code    — one-shot by nature: you type it and press the
            #     button. Text captchas are `puzzle_source == "unknown"` (that is
            #     how text_mode is detected — no vendor frame serves a typed
            #     challenge), so without naming them here every clause below was
            #     False and the code sat in the box unsent, round after round,
            #     until the deadline reported "captcha still detected".
            #   no action/done  — submit to advance.
            #   a PLACED PIECE  — one-shot by nature, like a typed code. A drag
            #     with a SOURCE drops a piece into a hole: there is no count to
            #     reach that could auto-submit it and no release being graded,
            #     so nothing further will happen on its own. Nor is it ever
            #     followed by a `done` the way a click round is — with the board
            #     still unanswered the model keeps re-answering it, nudging the
            #     piece a pixel a round until the loop cap. That is
            #     `lemin_cropped`: ten loops, 95 s, and a correct placement made
            #     on the first one.
            #   a completed slide — ALREADY submitted. Letting go of the handle
            #     is the gesture these puzzles grade; none of them has a Verify
            #     button, so anything the generic finder turns up here belongs to
            #     the host page, and pressing it would submit the form the
            #     captcha guards while the verdict is still in flight.
            #   a CLICK round   — deliberately absent. These boards re-round,
            #     and submitting a half-made selection spends the attempt; they
            #     get their press on the round the model answers `done`.
            # (reCAPTCHA 3x3 never reaches here; it returned above.)
            should_submit = not slid and (
                not performed_action or typed or placed
                or puzzle_source == "hcaptcha" or is_recaptcha_one_shot
            )
            if should_submit and verify_button:
                _log(f"clicking Verify to submit ({puzzle_source}).")
                self._move_and_click(page, verify_button)
                # The press IS an interaction, and saying so is load-bearing:
                # the caller aborts a round that reports none, so submitting a
                # `done` answer and then returning False re-arms the very guard
                # this satisfies — the puzzle is sent and the solve gives up on
                # it one line later, which is what `prosopo_grid_3x3` did.
                performed_action = True
                # Snapshot at submit time so the NEXT attempt waits for the real
                # transition before treating whatever is on screen as fresh.
                self._last_submit_frame_hash = self._element_frame_hash(element)
        finally:
            _unlink(shot)
            # Only now: the wait gate re-reads the keyframe PNG on every poll, so
            # removing the directory any earlier would break the click it is gating.
            if keyframe_dir:
                shutil.rmtree(keyframe_dir, ignore_errors=True)

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
        start = time.monotonic() * 1000.0
        cumulative_usage: List[Dict[str, Any]] = []
        self._last_submit_frame_hash = None
        self._deadline_ms = start + self.config.overall_solve_timeout_ms
        self._viewport_cache = None
        self._cursor_seeded = False

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
            self._deadline_ms = None
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
                # Already satisfied before we touched anything — the widget is
                # present but the vendor has passed it (anchor checked / token
                # populated). Common with a good stealth browser: camoufox often
                # clears reCAPTCHA on the checkbox alone, with no challenge ever
                # shown. Without this, the render-wait branch below sits for
                # ~6s and then raises "no interactive captcha widget", turning
                # the BEST outcome into an exception the caller has to catch.
                if self.is_captcha_solved(page):
                    _log("captcha already satisfied; nothing to solve.")
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
                        # Used to be terminal. Now it just means the next round is an
                        # animated puzzle: retry the loop and `_solve_single` takes the
                        # recording path. `unsupported_retries` still bounds it, so a
                        # widget that is animated AND unsolvable cannot spin here.
                        if not cfg.video_solve_enabled:
                            raise AnimatedChallengeError(
                                "the challenge never settles and video_solve_enabled is off"
                            )
                        _log('"unsupported" mid-solve and the next round is animated; '
                             "retrying into the recording path.")
                        continue
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
                widget_gone = 0
                while time.monotonic() * 1000.0 < deadline:
                    if self.is_captcha_solved(page):
                        solved = True
                        break
                    # The eight inline vendors have no response token, so
                    # `is_captcha_solved` — which reads only the hCaptcha and
                    # reCAPTCHA anchors — can never fire for them and this loop
                    # ran out its whole 2.5s budget on EVERY round, waiting for
                    # a signal that cannot arrive. Measured on geetest_v4_slide:
                    # 5.2s of a 12.3s solve, spent after the puzzle was already
                    # answered, with the widget sitting there visibly solved.
                    #
                    # "The widget is gone" is the completion signal for those
                    # vendors and is already the authority immediately after
                    # this loop, so this only reaches the same verdict sooner —
                    # confirmed over two polls so a frame caught mid-swap
                    # between rounds cannot read as a solve.
                    if self.detect_captcha(page) is None:
                        widget_gone += 1
                        if widget_gone >= 2:
                            solved = True
                            break
                    else:
                        widget_gone = 0
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


def _bbox_center(bbox: Sequence[float]) -> Tuple[float, float]:
    """Centre of an [x1, y1, x2, y2] 0–1 box, as the (x, y) 0–1 point the keyframe
    wait gate compares around. The solver builds these boxes as a small square
    around the model's point, so the centre recovers that point exactly."""
    x1, y1, x2, y2 = (float(v) for v in bbox)
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


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
