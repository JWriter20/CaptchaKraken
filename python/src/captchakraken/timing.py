"""Where one solve's wall-clock went, by phase. Always collected, printed under CAPTCHA_TIMINGS=1."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Iterator

PRODUCTIVE = {"inference", "mouse"}


def timings_enabled() -> bool:
    return os.getenv("CAPTCHA_TIMINGS", "0") == "1"


class PhaseBudget:
    def __init__(self) -> None:
        self.totals: dict = {}
        self.counts: dict = {}
        self._open: list = []
        self._t0 = time.perf_counter()

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        # Only the outermost entry of a name accumulates; nesting under another name counts under both.
        if name in self._open:
            yield
            return
        self._open.append(name)
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self._open.remove(name)
            self.add(name, (time.perf_counter() - t0) * 1000.0)

    def add(self, name: str, ms: float) -> None:
        self.totals[name] = self.totals.get(name, 0.0) + ms
        self.counts[name] = self.counts.get(name, 0) + 1

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0

    def report(self) -> str:
        total = self.elapsed_ms()
        useful = sum(v for k, v in self.totals.items() if k in PRODUCTIVE)
        lines = [f"[BUDGET] solve {total / 1000:.1f}s — {useful / 1000:.1f}s useful "
                 f"({100 * useful / total if total else 0:.0f}%), {(total - useful) / 1000:.1f}s waiting"]
        for name, ms in sorted(self.totals.items(), key=lambda kv: -kv[1]):
            tag = "*" if name in PRODUCTIVE else " "
            lines.append(f"[BUDGET] {tag} {name:22s} {ms / 1000:6.2f}s  x{self.counts[name]}")
        unattributed = total - sum(self.totals.values())
        if unattributed > 50:
            lines.append(f"[BUDGET]   {'(unattributed)':22s} {unattributed / 1000:6.2f}s")
        return "\n".join(lines)
