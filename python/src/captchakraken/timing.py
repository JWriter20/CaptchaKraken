"""
Lightweight timing utilities.

Enable with env var:
  CAPTCHA_TIMINGS=1

This prints a single-line timing record to stderr for each instrumented step.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from typing import Iterator, Optional


def timings_enabled() -> bool:
    return os.getenv("CAPTCHA_TIMINGS", "0") == "1"


@contextmanager
def timed(label: str, extra: Optional[str] = None) -> Iterator[None]:
    """
    Context manager that prints:
      [TIMING] <label>: <ms>ms (<extra>)
    to stderr when CAPTCHA_TIMINGS=1.
    """
    if not timings_enabled():
        yield
        return

    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt_ms = (time.perf_counter() - t0) * 1000.0
        suffix = f" ({extra})" if extra else ""
        print(f"[TIMING] {label}: {dt_ms:.2f}ms{suffix}", file=sys.stderr)


#: What a phase's time COUNTS AS, when asking how much of a solve was useful.
#:
#: Only two kinds of second are worth spending: one the model is thinking in,
#: and one the mouse is travelling in (which has to look human, so it cannot be
#: rushed). Everything else is the driver waiting on a clock, and every such
#: wait is a candidate for deletion or for overlapping with something real.
PRODUCTIVE = {"inference", "mouse"}


class PhaseBudget:
    """Where one solve's wall-clock went, by phase.

    Exists because "the solve took 77s" is not actionable and "the settle
    monitor spent 31s of it" is. Always on — it is a few dict updates per phase
    against multi-second waits — but only PRINTED under CAPTCHA_TIMINGS=1.

    Phases may nest (a burst contains its screenshots). Only the outermost of a
    GIVEN NAME accumulates, so re-entering one cannot double-count it — but a
    phase nested inside a differently-named one counts under both, deliberately:
    the cursor drifting over the widget WHILE the model generates is genuinely
    both `mouse` and `inference`, and hiding either would misreport what the
    solve was doing. The totals are therefore an attribution, not a partition,
    and can exceed the elapsed time. `report()` prints the residual as
    `(unattributed)` and it can go negative when they do.
    """

    def __init__(self) -> None:
        self.totals: dict = {}
        self.counts: dict = {}
        self._open: list = []
        self._t0 = time.perf_counter()

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if name in self._open:          # nested re-entry: attribute it to the outer one
            yield
            return
        self._open.append(name)
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self._open.remove(name)
            dt = (time.perf_counter() - t0) * 1000.0
            self.totals[name] = self.totals.get(name, 0.0) + dt
            self.counts[name] = self.counts.get(name, 0) + 1

    def add(self, name: str, ms: float) -> None:
        """Record a measured span directly.

        For blocks that would need re-indenting to sit under `phase()` — the
        accounting is not worth reshaping working control flow over.
        """
        self.totals[name] = self.totals.get(name, 0.0) + ms
        self.counts[name] = self.counts.get(name, 0) + 1

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0

    def report(self) -> str:
        total = self.elapsed_ms()
        useful = sum(v for k, v in self.totals.items() if k in PRODUCTIVE)
        rows = sorted(self.totals.items(), key=lambda kv: -kv[1])
        lines = [f"[BUDGET] solve {total / 1000:.1f}s — "
                 f"{useful / 1000:.1f}s useful ({100 * useful / total if total else 0:.0f}%), "
                 f"{(total - useful) / 1000:.1f}s waiting"]
        for name, ms in rows:
            tag = "*" if name in PRODUCTIVE else " "
            lines.append(f"[BUDGET] {tag} {name:22s} {ms / 1000:6.2f}s  x{self.counts[name]}")
        unattributed = total - sum(self.totals.values())
        if unattributed > 50:
            lines.append(f"[BUDGET]   {'(unattributed)':22s} {unattributed / 1000:6.2f}s")
        return "\n".join(lines)


