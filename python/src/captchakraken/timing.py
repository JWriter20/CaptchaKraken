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


