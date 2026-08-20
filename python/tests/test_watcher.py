"""
The auto-solve watcher's contract, driven against a fake solver.

A fake rather than a browser for the same reason the page-driver tests use one:
everything worth pinning here — does it stop, does it re-arm, does it back off,
does a caller's own exception kill the loop — is a property of the LOOP, and a
real page would make each assertion slow and flaky without testing anything
extra.

Twin of `js/src/watcher.test.ts`. The two ports differ in one deliberate way
(this one blocks; see watcher.py), so the cases are matched but `run()` is
driven with a timeout where the TS twin awaits a background handle.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from captchakraken.watcher import CaptchaWatcher  # noqa: E402

RESULT = object()


class FakePage:
    def __init__(self) -> None:
        self._closed = False

    def is_closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True


class FakeSolver:
    """Detects a captcha every time and solves it, unless told otherwise."""

    def __init__(self, detect: Any = True, solve: Any = RESULT) -> None:
        self._detect = detect
        self._solve = solve
        self.detects = 0
        self.solve_calls = 0

    def detect_captcha(self, page: Any) -> Any:
        self.detects += 1
        return self._detect(page) if callable(self._detect) else self._detect

    def solve(self, page: Any) -> Any:
        self.solve_calls += 1
        if callable(self._solve):
            return self._solve(page)
        return self._solve


def watcher(solver: Any, page: Any, **kw: Any) -> CaptchaWatcher:
    kw.setdefault("interval_ms", 5)
    kw.setdefault("error_backoff_ms", 20)
    return CaptchaWatcher(solver=solver, page=page, **kw)


def test_solves_a_detected_captcha_and_reports_it() -> None:
    seen: List[Any] = []
    solver = FakeSolver()
    w = watcher(solver, FakePage(), on_solved=seen.append, max_solves=2)

    assert w.run(timeout_ms=2000) == 2
    assert solver.solve_calls == 2
    assert seen == [RESULT, RESULT], "every solve should be reported exactly once"


def test_does_not_solve_when_nothing_is_detected() -> None:
    solver = FakeSolver(detect=None)
    w = watcher(solver, FakePage())

    w.run(timeout_ms=60)

    assert solver.detects > 0, "never even probed"
    assert solver.solve_calls == 0, "solved with no captcha present — a billable request for nothing"


def test_poll_once_does_not_sleep_and_returns_the_result() -> None:
    solver = FakeSolver()
    w = watcher(solver, FakePage(), interval_ms=10_000)

    start = time.monotonic()
    result = w.poll_once()

    assert result is RESULT
    assert w.solves == 1
    assert time.monotonic() - start < 0.5, "poll_once must not wait an interval; run() owns the cadence"


def test_poll_once_returns_none_when_no_captcha() -> None:
    w = watcher(FakeSolver(detect=None), FakePage())
    assert w.poll_once() is None
    assert w.solves == 0


def test_a_failing_solve_is_reported_and_the_watcher_survives() -> None:
    errors: List[BaseException] = []

    def boom(page: Any) -> Any:
        raise RuntimeError("unsupported challenge")

    solver = FakeSolver(solve=boom)
    w = watcher(solver, FakePage(), on_error=errors.append, error_backoff_ms=5)

    w.run(timeout_ms=200)

    assert errors, "error was never reported"
    assert str(errors[0]) == "unsupported challenge"
    assert solver.solve_calls > 1, "watcher gave up after the first failure"


def test_a_failing_solve_backs_off_instead_of_hot_looping() -> None:
    def boom(page: Any) -> Any:
        raise RuntimeError("nope")

    solver = FakeSolver(solve=boom)
    w = watcher(solver, FakePage(), interval_ms=1, error_backoff_ms=80)

    w.run(timeout_ms=250)

    # Without the backoff this runs on every 1ms tick. The assertion is about
    # the order of magnitude, not an exact count.
    assert solver.solve_calls <= 5, f"backoff not applied: {solver.solve_calls} attempts"


def test_an_idle_tick_does_not_pay_the_error_backoff() -> None:
    """The bug the `(result, failed)` split in _attempt() exists to prevent."""
    solver = FakeSolver(detect=None)
    w = watcher(solver, FakePage(), interval_ms=1, error_backoff_ms=5000)

    w.run(timeout_ms=120)

    # If "no captcha" were treated as a failure, the first tick would sleep for
    # five seconds and this would probe once.
    assert solver.detects > 3, f"idle ticks are paying the error backoff: {solver.detects} probes"


def test_max_solves_stops_the_watcher() -> None:
    solver = FakeSolver()
    w = watcher(solver, FakePage(), max_solves=3)

    assert w.run(timeout_ms=2000) == 3
    assert w.running is False
    assert solver.solve_calls == 3


def test_a_closed_page_ends_the_watcher() -> None:
    page = FakePage()
    solver = FakeSolver(detect=None)
    w = watcher(solver, page)

    page.close()
    w.run(timeout_ms=200)

    assert w.running is False, "watcher outlived its page"


def test_a_page_closed_mid_solve_stops_rather_than_reporting_an_error() -> None:
    page = FakePage()
    errors: List[BaseException] = []

    def close_then_raise(_: Any) -> Any:
        page.close()
        raise RuntimeError("Target page, context or browser has been closed")

    w = watcher(FakeSolver(solve=close_then_raise), page, on_error=errors.append)
    w.run(timeout_ms=200)

    assert w.running is False
    assert errors == [], "a teardown race should end the watcher, not be reported as a solve failure"


def test_stop_from_a_callback_ends_the_loop() -> None:
    solver = FakeSolver()
    w = watcher(solver, FakePage())
    w.on_solved = lambda _: w.stop()

    w.run(timeout_ms=2000)

    assert w.solves == 1, "stop() from on_solved did not take effect"
    assert w.running is False


def test_a_throwing_callback_does_not_stop_the_watcher() -> None:
    calls = {"n": 0}

    def bad(_: Any) -> None:
        calls["n"] += 1
        raise ValueError("caller bug")

    w = watcher(FakeSolver(), FakePage(), on_solved=bad, max_solves=3)
    w.run(timeout_ms=2000)

    assert calls["n"] == 3, "the caller's own exception killed the loop"


def test_keyboard_interrupt_propagates() -> None:
    """Ctrl-C during a solve must reach the caller, not be swallowed as an error."""

    def interrupt(_: Any) -> Any:
        raise KeyboardInterrupt

    w = watcher(FakeSolver(solve=interrupt), FakePage(), on_error=lambda e: None)

    with pytest.raises(KeyboardInterrupt):
        w.run(timeout_ms=500)


def test_stop_is_idempotent_and_safe_before_the_first_tick() -> None:
    w = watcher(FakeSolver(), FakePage(), interval_ms=10_000)
    w.stop()
    w.stop()
    assert w.running is False
    assert w.run(timeout_ms=100) == 0


def test_a_launcher_without_is_closed_is_treated_as_open() -> None:
    """Duck-typing: not every Playwright-compatible page exposes is_closed."""

    class Bare:
        pass

    w = watcher(FakeSolver(), Bare(), max_solves=1)
    assert w.run(timeout_ms=1000) == 1
