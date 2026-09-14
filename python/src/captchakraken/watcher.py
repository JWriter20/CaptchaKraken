"""Auto-solve watcher, the Python mirror of `js/src/watcher.ts`.

A poll, not an injected MutationObserver: an exposed binding is a function on `window` a captcha vendor can
enumerate on vanilla Playwright or patchright, so this injects nothing and drives `detect_captcha()` on a timer.
It blocks where the TS port does not because a sync Playwright handle is bound to the greenlet that created it.
One watcher covers a page for its whole life, across every `goto` (pinned in test_browser_compat.py).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple

__all__ = ["CaptchaWatcher"]


def _page_is_closed(page: Any) -> bool:
    for name in ("is_closed", "isClosed"):
        probe = getattr(page, name, None)
        if callable(probe):
            try:
                return bool(probe())
            except Exception:
                return False
    return False


def _report(callback: Optional[Callable[[Any], Any]], value: Any) -> None:
    if callback is None:
        return
    try:
        callback(value)
    except Exception:
        pass


@dataclass
class CaptchaWatcher:

    solver: Any
    page: Any
    # Below ~250ms is real CPU for latency the solve itself (seconds) makes irrelevant.
    interval_ms: int = 1000
    max_solves: Optional[int] = None
    # Without it a permanently unsupported challenge (an invisible reCAPTCHA v3) re-attempts and re-bills forever.
    error_backoff_ms: int = 5000
    on_solved: Optional[Callable[[Any], Any]] = None
    # Never fatal: NoCaptchaFoundError fires routinely when a widget vanishes between the probe and the solve.
    on_error: Optional[Callable[[BaseException], Any]] = None

    solves: int = field(default=0, init=False)
    _stopped: bool = field(default=False, init=False)

    @property
    def running(self) -> bool:
        if self._stopped or _page_is_closed(self.page):
            return False
        return self.max_solves is None or self.solves < self.max_solves

    def stop(self) -> None:
        self._stopped = True

    def poll_once(self) -> Optional[Any]:
        return self._attempt()[0]

    def _attempt(self) -> Tuple[Optional[Any], bool]:
        """`(result, failed)`: `poll_once` conflates "nothing" and "raised", and only the latter backs off."""
        if not self.running:
            return None, False
        # KeyboardInterrupt / SystemExit are deliberately not caught: Ctrl-C during a solve must reach the caller.
        try:
            if not self.solver.detect_captcha(self.page):
                return None, False
            result = self.solver.solve(self.page)
        except Exception as exc:
            if _page_is_closed(self.page):
                self.stop()
                return None, False
            _report(self.on_error, exc)
            return None, True
        if result is not None:
            self.solves += 1
            _report(self.on_solved, result)
        return result, False

    def _sleep(self, ms: float) -> None:
        deadline = time.monotonic() + ms / 1000.0
        while time.monotonic() < deadline:
            if self._stopped:
                return
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def run(self, timeout_ms: Optional[int] = None) -> int:
        deadline = None if timeout_ms is None else time.monotonic() + timeout_ms / 1000.0
        while self.running:
            if deadline is not None and time.monotonic() >= deadline:
                break
            self._sleep(self.interval_ms)
            if not self.running:
                break
            if self._attempt()[1]:
                self._sleep(self.error_backoff_ms)
        return self.solves
