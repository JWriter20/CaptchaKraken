"""
Hermetic tests for the Python page driver.

No browser, no GPU, no network. The driver duck-types the Playwright surface,
which is exactly what makes this testable: a fake page that implements the same
handful of methods exercises the real state machine. These cover the decisions
that were expensive to learn in the TypeScript driver and that a port is most
likely to get wrong.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from captchakraken.page_solver import (  # noqa: E402
    AnimatedChallengeError,
    CaptchaSolveError,
    NoCaptchaFoundError,
    PageSolver,
    PageSolverConfig,
    UnsupportedChallengeError,
    _aggregate,
    _as_dict,
    _read_png_dimensions,
)
from captchakraken.solver import UnsupportedCaptchaError  # noqa: E402
from captchakraken.trajectory import generate_trajectory  # noqa: E402


# ---------------------------------------------------------------- fake page


class FakeElement:
    def __init__(
        self,
        src: str = "",
        visible: bool = True,
        box: Optional[Dict[str, float]] = None,
        frame: Optional["FakeFrame"] = None,
        text: str = "",
    ) -> None:
        self._src = src
        self._visible = visible
        self._box = box or {"x": 100.0, "y": 100.0, "width": 400.0, "height": 400.0}
        self._frame = frame
        self._text = text
        self.screenshots = 0

    def get_attribute(self, name: str) -> Optional[str]:
        return {"src": self._src, "value": None}.get(name)

    def is_visible(self) -> bool:
        return self._visible

    def bounding_box(self) -> Dict[str, float]:
        return self._box

    def content_frame(self) -> Optional["FakeFrame"]:
        return self._frame

    def text_content(self) -> str:
        return self._text

    def scroll_into_view_if_needed(self) -> None:
        pass

    def screenshot(self, path: str, **_: Any) -> None:
        self.screenshots += 1
        _write_png(path, 400, 400)


class FakeFrame:
    def __init__(self, elements: Optional[Dict[str, FakeElement]] = None) -> None:
        self._elements = elements or {}

    def query_selector(self, selector: str) -> Optional[FakeElement]:
        for key, element in self._elements.items():
            if key in selector or selector in key:
                return element
        return None

    def wait_for_selector(self, *_: Any, **__: Any) -> None:
        pass

    def wait_for_function(self, *_: Any, **__: Any) -> None:
        pass


class FakeMouse:
    def __init__(self) -> None:
        self.moves: List[tuple] = []
        self.clicks = 0

    def move(self, x: float, y: float) -> None:
        self.moves.append((x, y))

    def down(self) -> None:
        pass

    def up(self) -> None:
        self.clicks += 1


class FakePage:
    def __init__(self, elements: Optional[Dict[str, FakeElement]] = None) -> None:
        self._elements = elements or {}
        self.mouse = FakeMouse()
        self.viewport_size = {"width": 1280, "height": 800}

    def query_selector(self, selector: str) -> Optional[FakeElement]:
        return self._elements.get(selector)

    def query_selector_all(self, selector: str) -> List[FakeElement]:
        element = self._elements.get(selector)
        return [element] if element else []

    def eval_on_selector(self, *_: Any, **__: Any) -> str:
        return ""


def _write_png(path: str, width: int, height: int) -> None:
    """A byte-valid PNG header — enough for _read_png_dimensions and file I/O."""
    ihdr = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        handle.write(struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00\x00\x00\x00")


RECAPTCHA_BFRAME = 'iframe[src*="recaptcha/api2/bframe"]'
RECAPTCHA_ANCHOR = 'iframe[src*="recaptcha/api2/anchor"]'
HCAPTCHA_CHALLENGE = 'iframe[src*="hcaptcha"][src*="frame=challenge"]'
HCAPTCHA_CHECKBOX = 'iframe[src*="hcaptcha"][src*="frame=checkbox"]'


def _solver(**overrides: Any) -> PageSolver:
    """A PageSolver whose model half is stubbed — no CaptchaSolver constructed."""
    solver = PageSolver.__new__(PageSolver)
    solver.config = PageSolverConfig(**overrides)
    solver._solver = None
    solver._last_mouse = (0.0, 0.0)
    solver._last_submit_frame_hash = None
    return solver


# ---------------------------------------------------------------- detection


class TestDetection:
    def test_prefers_the_open_challenge_over_the_checkbox(self):
        # An open image challenge and an unchecked anchor can be on the page at
        # once. Solving the anchor again just reopens what is already open.
        challenge = FakeElement(src="https://google.com/recaptcha/api2/bframe?k=x")
        anchor = FakeElement(src="https://google.com/recaptcha/api2/anchor?k=x")
        page = FakePage({RECAPTCHA_BFRAME: challenge, RECAPTCHA_ANCHOR: anchor})
        assert _solver().detect_captcha(page) is challenge

    def test_ignores_an_already_checked_recaptcha_anchor(self):
        frame = FakeFrame({".recaptcha-checkbox-checked": FakeElement()})
        anchor = FakeElement(src="recaptcha/api2/anchor", frame=frame)
        page = FakePage({RECAPTCHA_ANCHOR: anchor})
        assert _solver().detect_captcha(page) is None

    def test_hcaptcha_checkbox_solved_via_aria_checked_without_a_token(self):
        # Demo pages don't always populate h-captcha-response, so the anchor's
        # visual state is the necessary tie-breaker, not a nicety.
        frame = FakeFrame({'#checkbox[aria-checked="true"]': FakeElement()})
        checkbox = FakeElement(src="https://hcaptcha.com/?frame=checkbox", frame=frame)
        page = FakePage({HCAPTCHA_CHECKBOX: checkbox})
        assert _solver().detect_captcha(page) is None
        assert _solver().is_captcha_solved(page) is True

    def test_invisible_recaptcha_is_not_an_interactive_widget(self):
        # v3 / invisible-v2 injects only an anchor with size=invisible and never
        # a challenge frame. Treating it as "still loading" hangs for the whole
        # render-wait budget on every v3 page.
        anchor = FakeElement(src="https://google.com/recaptcha/api2/anchor?k=x&size=invisible")
        page = FakePage({RECAPTCHA_ANCHOR: anchor})
        assert _solver().has_interactive_widget_in_dom(page) is False

    def test_visible_v2_anchor_is_an_interactive_widget(self):
        anchor = FakeElement(src="https://google.com/recaptcha/api2/anchor?k=x")
        page = FakePage({RECAPTCHA_ANCHOR: anchor})
        assert _solver().has_interactive_widget_in_dom(page) is True


class TestSolveLoop:
    def test_no_widget_at_all_fails_fast(self):
        with pytest.raises(NoCaptchaFoundError):
            _solver().solve(FakePage())

    def test_nothing_left_after_interacting_is_success(self):
        # detect_captcha() == None means two different things depending on
        # whether we have interacted. Post-interaction it means solved.
        solver = _solver()
        page = FakePage()
        solver._has_interacted_probe = True
        challenge = FakeElement(src="recaptcha/api2/bframe")
        pages = [challenge, None]

        def fake_detect(_page):
            return pages.pop(0) if pages else None

        solver.detect_captcha = fake_detect  # type: ignore[method-assign]
        solver._solve_single = lambda *_: (True, [])  # type: ignore[method-assign]
        solver.is_captcha_solved = lambda _page: False  # type: ignore[method-assign]
        solver._is_challenge_freshly_rendered = lambda _page: False  # type: ignore[method-assign]
        solver._has_recaptcha_underselect_error = lambda _page: False  # type: ignore[method-assign]

        result = solver.solve(page)
        assert result.is_solved is True

    def test_no_interaction_with_captcha_still_present_aborts(self):
        # Otherwise the loop spins until the overall timeout doing nothing.
        solver = _solver()
        solver.detect_captcha = lambda _page: FakeElement(src="recaptcha/api2/bframe")  # type: ignore[method-assign]
        solver._solve_single = lambda *_: (False, [])  # type: ignore[method-assign]
        solver.is_captcha_solved = lambda _page: False  # type: ignore[method-assign]
        solver._has_recaptcha_underselect_error = lambda _page: False  # type: ignore[method-assign]
        with pytest.raises(CaptchaSolveError, match="no interactions"):
            solver.solve(FakePage())

    def test_unsupported_on_the_first_frame_is_definitive(self):
        # Before any interaction there is no transitional-frame excuse: fail.
        solver = _solver()
        solver.detect_captcha = lambda _page: FakeElement(src="https://hcaptcha.com/?frame=challenge")  # type: ignore[method-assign]

        def raise_unsupported(*_):
            raise UnsupportedCaptchaError("nope")

        solver._solve_single = raise_unsupported  # type: ignore[method-assign]
        with pytest.raises(UnsupportedChallengeError):
            solver.solve(FakePage())

    def test_unsupported_mid_solve_retries_instead_of_aborting(self):
        # The "solves round 1, dies on round 2" bug: after interacting, an
        # `unsupported` verdict is usually a not-yet-settled next round.
        solver = _solver(max_unsupported_resolves=2)
        solver.detect_captcha = lambda _page: FakeElement(src="https://hcaptcha.com/?frame=challenge")  # type: ignore[method-assign]
        solver._wait_for_element_settled = lambda _el: "settled"  # type: ignore[method-assign]
        solver.is_captcha_solved = lambda _page: False  # type: ignore[method-assign]
        solver._is_challenge_freshly_rendered = lambda _page: False  # type: ignore[method-assign]
        solver._has_recaptcha_underselect_error = lambda _page: False  # type: ignore[method-assign]

        calls = {"n": 0}

        def flaky(*_):
            calls["n"] += 1
            if calls["n"] == 1:
                return True, []          # interact once
            raise UnsupportedCaptchaError("transitional blank frame")

        solver._solve_single = flaky  # type: ignore[method-assign]
        with pytest.raises(UnsupportedChallengeError):
            solver.solve(FakePage())
        # Retried rather than aborting on the first unsupported after interaction.
        assert calls["n"] >= 3

    def test_stale_handle_after_submit_is_retried_not_fatal(self):
        # hCaptcha swaps the iframe between rounds while we hold the old handle.
        solver = _solver(max_stale_element_retries=2, stale_element_backoff_ms=1)
        solver.detect_captcha = lambda _page: FakeElement(src="https://hcaptcha.com/?frame=challenge")  # type: ignore[method-assign]
        solver.is_captcha_solved = lambda _page: False  # type: ignore[method-assign]
        solver._is_challenge_freshly_rendered = lambda _page: False  # type: ignore[method-assign]
        solver._has_recaptcha_underselect_error = lambda _page: False  # type: ignore[method-assign]

        calls = {"n": 0}

        def flaky(*_):
            calls["n"] += 1
            if calls["n"] == 1:
                return True, []
            raise RuntimeError("Element is not attached to the DOM")

        solver._solve_single = flaky  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            solver.solve(FakePage())
        assert calls["n"] >= 3  # retried the stale handle before giving up

    def test_stale_handle_before_any_interaction_is_surfaced(self):
        # A first-frame failure is a real problem, not a round transition.
        solver = _solver()
        solver.detect_captcha = lambda _page: FakeElement(src="https://hcaptcha.com/?frame=challenge")  # type: ignore[method-assign]

        def raise_detached(*_):
            raise RuntimeError("Element is not attached to the DOM")

        solver._solve_single = raise_detached  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            solver.solve(FakePage())

    def test_underselect_error_retries_once_then_aborts(self):
        # reCAPTCHA does NOT refresh tiles on this error, so without the
        # missed-tiles prompt the model answers `done` forever and the solve
        # loops until timeout.
        solver = _solver()
        solver.detect_captcha = lambda _page: FakeElement(src="recaptcha/api2/bframe")  # type: ignore[method-assign]
        solver.is_captcha_solved = lambda _page: False  # type: ignore[method-assign]
        solver._is_challenge_freshly_rendered = lambda _page: False  # type: ignore[method-assign]
        solver._has_recaptcha_underselect_error = lambda _page: True  # type: ignore[method-assign]

        seen_retry_modes: List[Optional[str]] = []

        def record(_page, _el, retry_mode):
            seen_retry_modes.append(retry_mode)
            return True, []

        solver._solve_single = record  # type: ignore[method-assign]
        with pytest.raises(CaptchaSolveError, match="under-selection"):
            solver.solve(FakePage())
        # First pass has no hint; the retry carries missed-tiles.
        assert seen_retry_modes[0] is None
        assert "missed-tiles" in seen_retry_modes

    def test_solved_signal_short_circuits_the_loop(self):
        solver = _solver()
        solver.detect_captcha = lambda _page: FakeElement(src="recaptcha/api2/bframe")  # type: ignore[method-assign]
        solver._solve_single = lambda *_: (True, [])  # type: ignore[method-assign]
        solver.is_captcha_solved = lambda _page: True  # type: ignore[method-assign]
        assert solver.solve(FakePage()).is_solved is True


class TestFreshnessGuard:
    def test_reuses_the_answer_when_the_frame_held_still(self):
        solver = _solver()
        solver._frame_changed_since = lambda *_: False  # type: ignore[method-assign]
        calls = {"n": 0}

        def query(_path):
            calls["n"] += 1
            return [{"action": "done"}], [{"total_tokens": 5}]

        actions, usage = solver._solve_frame_freshness_guarded(FakeElement(), "/tmp/x.png", query)
        assert calls["n"] == 1
        assert actions == [{"action": "done"}]
        assert usage == [{"total_tokens": 5}]

    def test_resolves_and_merges_usage_when_the_frame_moved(self):
        # Both queries really happened and both were billed, so both must appear
        # — dropping the stale round's usage would under-report real spend.
        solver = _solver(max_stale_frame_resolves=1)
        solver._frame_changed_since = lambda *_: True  # type: ignore[method-assign]
        solver._screenshot = lambda *a, **k: _write_png(a[1] if len(a) > 1 else k["path"], 4, 4)  # type: ignore[method-assign]
        calls = {"n": 0}

        def query(_path):
            calls["n"] += 1
            return [{"action": f"round{calls['n']}"}], [{"total_tokens": calls["n"]}]

        actions, usage = solver._solve_frame_freshness_guarded(FakeElement(), "/tmp/x.png", query)
        assert calls["n"] == 2
        assert actions == [{"action": "round2"}]  # the DEVELOPED frame's answer
        assert usage == [{"total_tokens": 1}, {"total_tokens": 2}]

    def test_disabled_guard_never_requeries(self):
        solver = _solver(stale_frame_resolve_enabled=False)
        solver._frame_changed_since = lambda *_: True  # type: ignore[method-assign]
        calls = {"n": 0}

        def query(_path):
            calls["n"] += 1
            return [{"action": "done"}], []

        solver._solve_frame_freshness_guarded(FakeElement(), "/tmp/x.png", query)
        assert calls["n"] == 1


class TestGridGeometry:
    GRID = [
        [0, 0, 100, 100], [100, 0, 200, 100], [200, 0, 300, 100],
        [0, 100, 100, 200], [100, 100, 200, 200], [200, 100, 300, 200],
        [0, 200, 100, 300], [100, 200, 200, 300], [200, 200, 300, 300],
    ]

    def test_bbox_maps_to_a_row_major_one_indexed_cell(self):
        solver = _solver()
        # Centre of the middle cell -> cell 5.
        assert solver._bbox_to_cell([0.4, 0.4, 0.6, 0.6], self.GRID, 300, 300) == 5
        # Top-left -> cell 1.
        assert solver._bbox_to_cell([0.0, 0.0, 0.2, 0.2], self.GRID, 300, 300) == 1
        # Bottom-right -> cell 9.
        assert solver._bbox_to_cell([0.85, 0.85, 1.0, 1.0], self.GRID, 300, 300) == 9

    def test_bbox_outside_every_cell_is_none(self):
        # A gutter hit must not be silently attributed to a neighbouring tile;
        # callers click the raw bbox and skip per-tile tracking.
        sparse = [[0, 0, 10, 10]]
        assert _solver()._bbox_to_cell([0.9, 0.9, 0.95, 0.95], sparse, 300, 300) is None

    def test_clicked_cells_are_watched_first(self):
        # Ordering matters: the just-clicked tiles are the ones whose fade tells
        # us the refresh began.
        assert _solver()._order_by_priority([3, 7, 1], [7, 1]) == [7, 1, 3]

    def test_priority_cells_not_loading_are_dropped(self):
        assert _solver()._order_by_priority([3], [7, 1]) == [3]


class TestHelpers:
    def test_png_dimensions_from_ihdr(self, tmp_path):
        path = str(tmp_path / "a.png")
        _write_png(path, 321, 654)
        assert _read_png_dimensions(path) == (321, 654)

    def test_png_dimensions_rejects_non_png(self, tmp_path):
        path = str(tmp_path / "a.png")
        Path(path).write_bytes(b"not a png at all......")
        assert _read_png_dimensions(path) is None

    def test_aggregate_sums_rounds(self):
        assert _aggregate([
            {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        ]) == [{"rounds": 2, "prompt_tokens": 15, "completion_tokens": 3, "total_tokens": 18}]

    def test_aggregate_of_nothing_is_empty(self):
        assert _aggregate([]) == []

    def test_as_dict_accepts_pydantic_actions(self):
        from captchakraken.action_types import WaitAction

        assert _as_dict(WaitAction(action="wait", duration_ms=50))["duration_ms"] == 50

    def test_as_dict_passes_plain_dicts_through(self):
        assert _as_dict({"action": "done"}) == {"action": "done"}


class TestTrajectory:
    def test_lands_exactly_on_target(self):
        # Texture is fine everywhere except the last sample: drift there means
        # every click lands off-target.
        points, _ = generate_trajectory((0, 0), (640, 480))
        assert points[-1] == (640, 480)

    def test_timings_are_cumulative_and_monotonic(self):
        # The driver sleeps against these as absolute offsets, exactly as the TS
        # driver does. Per-step deltas here would make the cursor teleport.
        _, timings = generate_trajectory((0, 0), (800, 600))
        assert timings[0] == 0
        assert all(b >= a for a, b in zip(timings, timings[1:]))

    def test_path_is_not_a_straight_line(self):
        points, _ = generate_trajectory((0, 0), (600, 0))
        # A straight lerp would keep every y at 0.
        assert any(abs(y) > 1.0 for _, y in points[:-1])

    def test_zero_length_move_is_a_single_sample(self):
        points, timings = generate_trajectory((10, 10), (10, 10))
        assert points == [(10, 10)] and timings == [0.0]

    def test_longer_moves_take_longer(self):
        # Fitts's law, not a fixed step count.
        _, short = generate_trajectory((0, 0), (40, 0))
        _, long = generate_trajectory((0, 0), (1200, 0))
        assert long[-1] > short[-1]


class TestAlreadySolved:
    def test_an_already_satisfied_captcha_returns_solved_not_an_error(self):
        # The best outcome must not be an exception. A good stealth browser
        # often clears reCAPTCHA on the checkbox alone, so the widget is in the
        # DOM, nothing is detectable to solve, and we have not interacted —
        # previously the render-wait branch then raised NoCaptchaFoundError.
        solver = _solver()
        solver.detect_captcha = lambda _page: None  # type: ignore[method-assign]
        solver.is_captcha_solved = lambda _page: True  # type: ignore[method-assign]
        solver.has_interactive_widget_in_dom = lambda _page: True  # type: ignore[method-assign]
        assert solver.solve(FakePage()).is_solved is True

    def test_an_unrendered_widget_still_waits_then_fails(self):
        # The other side of the same branch must keep working: present but not
        # solved and not rendered -> wait, then fail rather than hang.
        solver = _solver()
        solver.detect_captcha = lambda _page: None  # type: ignore[method-assign]
        solver.is_captcha_solved = lambda _page: False  # type: ignore[method-assign]
        solver.has_interactive_widget_in_dom = lambda _page: True  # type: ignore[method-assign]
        with pytest.raises(NoCaptchaFoundError):
            solver.solve(FakePage())
