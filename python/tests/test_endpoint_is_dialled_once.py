"""One pooled session: a fresh connection measured p50 258ms, pooled reuse p50 144ms. Remote endpoints are never /health-checked, because a hosted gateway serves none."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from captchakraken import server_manager
from captchakraken.planner import ActionPlanner


def _planner() -> ActionPlanner:
    return ActionPlanner(model="captcha", base_url="http://127.0.0.1:9/v1", api_key="k")


def test_the_planner_holds_one_session_for_every_request():
    assert isinstance(_planner()._http, requests.Session), (
        "the planner has no connection pool, so every inference re-dials the "
        "endpoint — ~110ms each, measured, and a TLS handshake against a "
        "hosted one")


def test_the_planner_never_posts_outside_that_session():
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
