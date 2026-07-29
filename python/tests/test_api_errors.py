"""The hosted API's refusals must read as CaptchaKraken problems, not vLLM ones.

Before this existed, a camoufox user whose credits ran out saw:

    vLLM 402 Payment Required at https://api.captchakraken.com/v1/chat/completions

naming a piece of infrastructure they had never installed and giving them
nothing to do about it. These tests pin the two properties that fix matters for:

  * a hosted refusal names CaptchaKraken and, where one exists, the URL that
    resolves it;
  * a SELF-HOSTED failure is untouched — no mention of credits or dashboards to
    someone running their own vLLM, and the bearer-token hint on 401/403 that
    was there before is still there.

Hermetic: a stub response object stands in for `requests`. Asserting against a
live zero-balance account would need a live zero-balance account, which is both
awkward to keep and a test of the server rather than of this client.
"""
import json

import pytest

from captchakraken import errors
from captchakraken.errors import CaptchaKrakenAPIError


class FakeResponse:
    """The slice of `requests.Response` that `from_response` actually reads."""

    def __init__(self, status_code, payload=None, *, text=None, headers=None, reason=""):
        self.status_code = status_code
        self.reason = reason
        self.headers = headers or {}
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def gateway(status, code, message, *, resolution_url=None, headers=None, reason=""):
    """A response shaped like the gateway's OpenAI-style error envelope."""
    error = {"message": message, "type": "billing_error", "code": code}
    if resolution_url:
        error["resolution_url"] = resolution_url
    return FakeResponse(status, {"error": error}, headers=headers, reason=reason)


# ── The headline case: out of credits ───────────────────────────────────────
def test_402_names_the_product_and_the_top_up_url():
    resp = gateway(
        402,
        "insufficient_credits",
        "Out of credits. Top up to resume solving.",
        resolution_url="https://captchakraken.com/billing",
    )
    exc = errors.from_response(resp, "https://api.captchakraken.com/v1/chat/completions")

    assert isinstance(exc, CaptchaKrakenAPIError)
    message = str(exc)
    assert "CaptchaKraken" in message
    assert "https://captchakraken.com/billing" in message
    # The whole point: the user must not be told about vLLM.
    assert "vLLM" not in message
    assert exc.code == "insufficient_credits"
    assert exc.status == 402
    assert exc.resolution_url == "https://captchakraken.com/billing"


def test_402_without_a_resolution_url_still_points_somewhere():
    exc = errors.from_response(
        gateway(402, "insufficient_credits", "Out of credits."), "http://x/v1/chat/completions"
    )
    assert "captchakraken.com" in str(exc)


# ── The other four a real user hits ─────────────────────────────────────────
def test_409_solve_abandoned_blames_fingerprint_not_the_answers():
    exc = errors.from_response(
        gateway(409, "solve_abandoned", "…served 10 times…"), "http://x/v1/chat/completions"
    )
    message = str(exc)
    assert "CaptchaKraken" in message
    # The actionable half: it is almost never the model's answers.
    assert "fingerprint" in message
    assert "x-ck-session" in message
    assert exc.code == "solve_abandoned"


def test_429_honours_retry_after():
    exc = errors.from_response(
        gateway(429, "rate_limited", "Too many requests.", headers={"Retry-After": "30"}),
        "http://x/v1/chat/completions",
    )
    assert exc.retry_after_seconds == 30
    assert "30s" in str(exc)


def test_429_without_retry_after_does_not_invent_one():
    exc = errors.from_response(
        gateway(429, "rate_limited", "Too many requests."), "http://x/v1/chat/completions"
    )
    assert exc.retry_after_seconds is None
    assert "Back off" in str(exc)


def test_retry_after_http_date_is_dropped_rather_than_guessed():
    # A confidently wrong "wait 3 seconds" is worse than saying nothing.
    exc = errors.from_response(
        gateway(
            429,
            "rate_limited",
            "Too many requests.",
            headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"},
        ),
        "http://x/v1/chat/completions",
    )
    assert exc.retry_after_seconds is None


def test_403_account_suspended_points_at_support():
    exc = errors.from_response(
        gateway(
            403,
            "account_suspended",
            "This account is suspended.",
            resolution_url="https://captchakraken.com/support",
        ),
        "http://x/v1/chat/completions",
    )
    assert "suspended" in str(exc)
    assert "https://captchakraken.com/support" in str(exc)
    assert exc.code == "account_suspended"


def test_413_request_too_large_suggests_the_fix():
    exc = errors.from_response(
        gateway(413, "request_too_large", "Request body exceeds the 8000000-byte limit."),
        "http://x/v1/chat/completions",
    )
    assert "CaptchaKraken" in str(exc)
    assert "captcha element" in str(exc)


def test_401_invalid_key_mentions_the_mcp_path():
    # Someone onboarded through the MCP has no env var to check, so pointing
    # only at CAPTCHA_KRAKEN_API_KEY would send them looking in the wrong place.
    exc = errors.from_response(
        gateway(401, "invalid_api_key", "Invalid API key."), "http://x/v1/chat/completions"
    )
    assert "create_api_key" in str(exc)
    assert "~/.captchakraken/credentials" in str(exc)


# ── Forward compatibility ───────────────────────────────────────────────────
def test_an_unknown_code_carries_the_servers_own_message_through():
    # The eleventh code must not be reported worse than the ten known ones.
    exc = errors.from_response(
        gateway(
            400,
            "some_code_invented_next_year",
            "The widget frobnicator is misaligned.",
            resolution_url="https://captchakraken.com/docs/frob",
        ),
        "http://x/v1/chat/completions",
    )
    assert isinstance(exc, CaptchaKrakenAPIError)
    assert "The widget frobnicator is misaligned." in str(exc)
    assert "https://captchakraken.com/docs/frob" in str(exc)
    assert exc.code == "some_code_invented_next_year"


# ── The self-hosted path must not regress ───────────────────────────────────
def test_a_plain_vllm_500_keeps_the_old_message():
    resp = FakeResponse(500, None, text="Internal Server Error", reason="Internal Server Error")
    exc = errors.from_response(resp, "http://localhost:8000/v1/chat/completions")

    assert not isinstance(exc, CaptchaKrakenAPIError)
    message = str(exc)
    assert "vLLM 500" in message
    assert "http://localhost:8000/v1/chat/completions" in message
    # Nobody self-hosting should be told to top up an account.
    assert "credits" not in message


def test_a_plain_vllm_401_keeps_the_bearer_token_hint():
    resp = FakeResponse(401, None, text='{"error":"Unauthorized"}', reason="Unauthorized")
    exc = errors.from_response(resp, "http://localhost:8000/v1/chat/completions")
    assert not isinstance(exc, CaptchaKrakenAPIError)
    assert "CAPTCHA_KRAKEN_API_KEY" in str(exc)


def test_an_html_error_page_does_not_crash_the_parser():
    # A proxy in front of a local server returns HTML, not JSON. `resp.json()`
    # raising must degrade to the generic message, not propagate.
    resp = FakeResponse(502, None, text="<html><body>Bad Gateway</body></html>", reason="Bad Gateway")
    exc = errors.from_response(resp, "http://localhost:8000/v1/chat/completions")
    assert not isinstance(exc, CaptchaKrakenAPIError)
    assert "vLLM 502" in str(exc)


def test_json_without_an_error_object_is_treated_as_self_hosted():
    resp = FakeResponse(400, {"detail": "something else entirely"}, reason="Bad Request")
    exc = errors.from_response(resp, "http://localhost:8000/v1/chat/completions")
    assert not isinstance(exc, CaptchaKrakenAPIError)


# ── The CLI/JS boundary ─────────────────────────────────────────────────────
def test_payload_round_trips_the_machine_readable_fields():
    # The JS driver rebuilds the error from exactly this; losing a field here
    # means camoufox users lose the top-up link.
    exc = CaptchaKrakenAPIError(
        "CaptchaKraken: out of credits.",
        status=402,
        code="insufficient_credits",
        resolution_url="https://captchakraken.com/billing",
        retry_after_seconds=None,
    )
    payload = exc.to_payload()
    assert payload["error"] == "CaptchaKraken: out of credits."
    assert payload["ck_error"]["code"] == "insufficient_credits"
    assert payload["ck_error"]["status"] == 402
    assert payload["ck_error"]["resolution_url"] == "https://captchakraken.com/billing"
    # Must survive json.dumps — it is written to stderr as one line.
    assert json.loads(json.dumps(payload)) == payload
