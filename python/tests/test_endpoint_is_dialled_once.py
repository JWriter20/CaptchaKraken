"""Two round trips per solve that bought nothing.

1. NO CONNECTION WAS EVER REUSED. Every inference went through a bare
   `requests.post`, which opens a new TCP connection — and, to a hosted HTTPS
   endpoint, does a new TLS handshake on top. Measured against this project's
   own endpoint, 8 requests each way:

       fresh connection   p50 258ms
       pooled reuse       p50 144ms

   ~110ms per inference, paid again on every round of a multi-round grid, for
   a server the process had finished talking to a second earlier.

2. `ensure_server` ASKED A REMOTE SERVER FOR /health BEFORE DECIDING IT WAS
   REMOTE. The function's whole job is to boot a LOCAL vLLM if one is not up;
   for an endpoint we do not manage there is nothing to ensure and the very
   next line returns. So the health GET was a round trip spent to reach a
   `return` — and up to its 2s timeout against a hosted gateway that serves no
   /health at all. Paid once per ActionPlanner, which for a caller using
   `solve_captcha_on_page` is once per solve.

Neither could show up as a failure: both are latency on a path that works.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from captchakraken import server_manager  # noqa: E402
from captchakraken.planner import ActionPlanner  # noqa: E402


def _planner() -> ActionPlanner:
    return ActionPlanner(model="captcha", base_url="http://127.0.0.1:9/v1", api_key="k")


def test_the_planner_holds_one_session_for_every_request():
    assert isinstance(_planner()._http, requests.Session), (
        "the planner has no connection pool, so every inference re-dials the "
        "endpoint — ~110ms each, measured, and a TLS handshake against a "
        "hosted one")


def test_the_planner_never_posts_outside_that_session():
    """The pool is inert if one call site still uses the module function."""
    source = (Path(__file__).resolve().parents[1]
              / "src" / "captchakraken" / "planner.py").read_text()
    assert "requests.post(" not in source, (
        "planner.py still calls requests.post directly; that call opens its own "
        "connection and ignores the session")


def test_a_remote_endpoint_is_not_health_checked(monkeypatch):
    called = []
    monkeypatch.setattr(server_manager, "is_healthy",
                        lambda *a, **k: called.append(a) or True)
    server_manager.ensure_server("http://13.57.41.42:8000/v1")
    assert called == [], (
        "ensure_server probed /health on a REMOTE endpoint. There is nothing to "
        "ensure there — the next line returns — so that is a round trip, or a "
        "2s timeout against a gateway that serves no /health, on every solve")


def test_a_local_endpoint_is_still_health_checked(monkeypatch):
    """The guard against 'just return early'."""
    called = []
    monkeypatch.setattr(server_manager, "is_healthy",
                        lambda *a, **k: called.append(a) or True)
    server_manager.ensure_server("http://127.0.0.1:8000/v1")
    assert called, "a local endpoint must still be probed — booting one is the job"


@pytest.mark.parametrize("url", ["http://localhost:8000/v1", "http://[::1]:8000/v1"])
def test_the_other_local_spellings_are_still_local(monkeypatch, url):
    called = []
    monkeypatch.setattr(server_manager, "is_healthy",
                        lambda *a, **k: called.append(a) or True)
    server_manager.ensure_server(url)
    assert called, f"{url} is a local endpoint and must still be probed"
