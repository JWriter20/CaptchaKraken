"""
Auto-solve watcher — the Python mirror of `js/src/watcher.ts`.

Install it once and captchas are solved as they appear, instead of the caller
having to know where in their script a challenge might show up.

WHY THIS IS A POLL AND NOT AN INJECTED OBSERVER
The obvious implementation is a `MutationObserver` in the page that calls out
through an exposed binding. That reacts faster, and it is also the one design
that cannot be made stealthy everywhere: an exposed binding puts a function on
`window`, and an observer is script the page can enumerate. Under Camoufox both
are sandboxed and invisible; under vanilla Playwright or patchright they are
not, and a captcha vendor is precisely the party that looks.

So this injects NOTHING. It drives the same `detect_captcha()` the solver
already uses, from the driver side, on a timer:

  - nothing is added to the page on ANY launcher, so there is no new detection
    surface to reason about per platform;
  - under Camoufox the probe's DOM reads land in its isolated Juggler world for
    free — that is Camoufox's default for all Playwright evaluation, with
    `main_world_eval` / an "mw:" prefix as the opt-OUT. This never opts out, so
    it is isolated there by construction rather than by special-casing;
  - reaction time is bounded by `interval_ms`, not by the mutation.

ONE WATCHER COVERS THE WHOLE PAGE, FOR ITS WHOLE LIFE
A page outlives navigation, and so does the watcher holding it: install it once
and it keeps probing across every `goto`, staying quiet while there is nothing
to solve and re-arming after each solve. That is deliberately the whole API --
the case that motivates a browser-wide installer is almost always a challenge
appearing on request 40 of a run, on the SAME page object the run started with,
and that is already covered by one line. Pinned in test_browser_compat.py.

WHY IT BLOCKS, WHERE THE TS PORT DOES NOT (CLAUDE.md 1c)
`solver.watch(page)` in TypeScript returns immediately and watches in the
background. It cannot here, and the reason is Playwright's, not ours: a SYNC
Playwright handle is bound to the greenlet that created it, so driving the page
from a worker thread raises. `threading` would not buy a background watcher, it
would buy an exception on the first probe.

The two shapes offered instead:

    solver.watch(page).run()          # blocking: hold this page clean
    watcher = solver.watch(page)
    while my_own_loop():
        watcher.poll_once()           # cooperative: you own the cadence

`poll_once()` is the one to reach for inside an existing automation loop; it
does exactly one probe, solves if there is something to solve, and returns.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple

__all__ = ["CaptchaWatcher"]


def _page_is_closed(page: Any) -> bool:
    """True when the launcher says the page is gone.

    Duck-typed like everything else that touches the page: Playwright and
    Puppeteer both expose `is_closed`/`isClosed`, but this module refuses to
    depend on either, and a launcher without it is simply treated as open.
    """
    for name in ("is_closed", "isClosed"):
        probe = getattr(page, name, None)
        if callable(probe):
            try:
                return bool(probe())
            except Exception:
                return False
    return False


def _report(callback: Optional[Callable[[Any], Any]], value: Any) -> None:
    """Hand a value to a user callback without letting its failure reach the loop."""
    if callback is None:
        return
    try:
        callback(value)
    except Exception:
        # A throwing callback is the caller's bug, not a reason to stop watching.
        pass


@dataclass
class CaptchaWatcher:
    """Polls one page and solves whatever captcha becomes visible on it."""

    solver: Any
    page: Any
    #: Milliseconds between probes. Each tick costs one `detect_captcha()` — a
    #: handful of selector queries. Below ~250ms you pay real CPU for latency
    #: the solve itself (seconds) makes irrelevant.
    interval_ms: int = 1000
    #: Stop after this many successful solves. None means unlimited.
    max_solves: Optional[int] = None
    #: Wait this long after a solve RAISES before probing again. Without it a
    #: permanently unsupported challenge — an invisible reCAPTCHA v3, say —
    #: becomes a hot loop that re-attempts and re-bills every interval forever.
    error_backoff_ms: int = 5000
    #: Called with the `SolveResult` of each completed solve.
    on_solved: Optional[Callable[[Any], Any]] = None
    #: Called with any exception from detection or solving. Errors are reported,
    #: never fatal: `NoCaptchaFoundError` fires routinely when a widget vanishes
    #: between the probe and the solve, and a watcher that died on it would be
    #: useless.
    on_error: Optional[Callable[[BaseException], Any]] = None

    solves: int = field(default=0, init=False)
    _stopped: bool = field(default=False, init=False)

    @property
    def running(self) -> bool:
        """False once stopped, the page closed, or `max_solves` was reached."""
        if self._stopped or _page_is_closed(self.page):
            return False
        return self.max_solves is None or self.solves < self.max_solves

    def stop(self) -> None:
        """End the loop. Safe to call from `on_solved` / `on_error`, or twice."""
        self._stopped = True

    def poll_once(self) -> Optional[Any]:
        """
        One probe. Solves if a captcha is visible; returns its `SolveResult`,
        or None when there was nothing to do (or the attempt failed and was
        reported to `on_error`).

        Does NOT sleep — the caller owns the cadence. `run()` is this plus the
        waiting and the backoff.
        """
        return self._attempt()[0]

    def _attempt(self) -> Tuple[Optional[Any], bool]:
        """
        `(result, failed)`.

        The second element is the whole reason this is not just `poll_once`:
        that method returns None both for "no captcha on the page" and for "a
        solve was attempted and raised", and `run()` must back off after the
        second while re-probing cheaply after the first. Collapsing the two
        either hot-loops on an unsupported challenge or adds a five-second
        stall to every idle tick.
        """
        if not self.running:
            return None, False
        try:
            if not self.solver.detect_captcha(self.page):
                return None, False
            result = self.solver.solve(self.page)
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            # KeyboardInterrupt / SystemExit are deliberately NOT caught: a
            # Ctrl-C during a long solve must reach the caller.
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
        """Sleep in slices so `stop()` and Ctrl-C are not held for a whole interval."""
        deadline = time.monotonic() + ms / 1000.0
        while time.monotonic() < deadline:
            if self._stopped:
                return
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def run(self, timeout_ms: Optional[int] = None) -> int:
        """
        Watch until `stop()`, the page closes, `max_solves` is hit, or
        `timeout_ms` elapses. Returns the number of captchas solved.

        Blocking by design — see this module's docstring on why the TypeScript
        port can hand back a background handle and this cannot.
        """
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
