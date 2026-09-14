"""A clock the burst loop is driven on: sleeping advances it and nothing else does, so a frame count is a fact about
the pacing code, not about the machine running the test."""

import time as real_time
from typing import Any


class VirtualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def perf_counter(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)

    def __getattr__(self, name: str) -> Any:
        return getattr(real_time, name)


def install(monkeypatch: Any, module: Any) -> VirtualClock:
    """Replace `module.time` with a virtual clock for the test's lifetime."""
    clock = VirtualClock()
    monkeypatch.setattr(module, "time", clock)
    return clock
