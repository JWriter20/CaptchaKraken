"""A refusal has to survive as something a caller can branch on.

The documented rule for consumers is "branch on `e.code`, never on the message
text". That only holds if the code, the status, the resolution URL and the
retry-after actually make it out of the HTTP response and across the process
boundary to the TypeScript driver, which can see nothing but stdout and stderr.

The failure these prevent is the one a customer hits: credits run out, and
instead of `insufficient_credits` the caller gets an unreadable stack from deep
inside the client and wraps a retry loop around a refusal that will never
succeed.
"""

import json

import pytest

from captchakraken.errors import CaptchaKrakenAPIError, from_response


class _Resp:
    """The parts of a requests.Response these functions actually read."""

    def __init__(self, status=400, payload=None, text="", headers=None, reason=""):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.headers = headers or {}
        self.reason = reason

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _gateway(code, message="nope", status=402, **kw):
    return _Resp(status=status, payload={"error": {"code": code, "message": message, **kw}})


def test_a_gateway_refusal_becomes_a_typed_error_carrying_its_code():
    err = from_response(_gateway("insufficient_credits", "You are out of credits."),
                        "https://api.example/v1/chat")

    assert isinstance(err, CaptchaKrakenAPIError)
    assert err.code == "insufficient_credits"
    assert err.status == 402
    assert "credit" in str(err).lower()


def test_the_resolution_url_survives_so_a_caller_can_show_it():
    err = from_response(
        _gateway("insufficient_credits", "out of credits", resolution_url="https://x.test/topup"),
        "https://api.example/v1",
    )
    assert err.resolution_url == "https://x.test/topup"


def test_retry_after_is_read_from_the_header_as_a_number():
    """`rate_limited` is the one refusal a caller SHOULD retry, and it can only
    honour the wait if the seconds arrive as a number rather than as prose."""
    resp = _Resp(status=429, payload={"error": {"code": "rate_limited", "message": "slow down"}},
                 headers={"Retry-After": "7"})
    err = from_response(resp, "https://api.example/v1")

    assert err.code == "rate_limited"
    assert err.retry_after_seconds == pytest.approx(7.0)


def test_a_refusal_with_no_message_still_reads_as_a_sentence():
    """An empty `message` from the gateway must not produce an error whose text
    is blank — that reaches a console as a bare traceback with no cause."""
    err = from_response(_gateway("account_suspended", ""), "https://api.example/v1")
    assert str(err).strip() != ""
    assert err.code == "account_suspended"


def test_a_self_hosted_error_page_is_not_dressed_up_as_a_gateway_refusal():
    """A local vLLM answers with HTML or plain text and no envelope. Inventing a
    `code` for it would send a self-hoster chasing a billing problem they do not
    have, so this stays a plain RuntimeError."""
    err = from_response(_Resp(status=500, payload=None, text="<html>Internal Error</html>",
                              reason="Internal Server Error"),
                        "http://localhost:8000/v1")

    assert not isinstance(err, CaptchaKrakenAPIError)
    assert isinstance(err, RuntimeError)
    assert "500" in str(err)


def test_a_local_401_names_the_variable_that_fixes_it():
    """The most common self-hosted setup failure. The message is the whole fix."""
    err = from_response(_Resp(status=401, payload=None, text="unauthorized"),
                        "http://localhost:8000/v1")
    assert "CAPTCHA_KRAKEN_API_KEY" in str(err)


def test_a_json_body_without_an_error_envelope_is_not_treated_as_a_refusal():
    """A proxy that answers 502 with `{"detail": ...}` has no code to offer."""
    err = from_response(_Resp(status=502, payload={"detail": "bad gateway"}),
                        "https://api.example/v1")
    assert not isinstance(err, CaptchaKrakenAPIError)


def test_the_payload_the_js_driver_reads_keeps_every_field():
    """This dict is the process boundary. If a field is dropped here, the JS port
    cannot reconstruct it and its `e.code` branch silently never matches."""
    err = CaptchaKrakenAPIError(
        "out of credits", status=402, code="insufficient_credits",
        resolution_url="https://x.test/topup", retry_after_seconds=3.5,
    )
    payload = err.to_payload()

    # It has to survive a real serialisation round trip, not just look right.
    round_tripped = json.loads(json.dumps(payload))
    assert round_tripped["error"] == "out of credits"
    assert round_tripped["ck_error"] == {
        "status": 402,
        "code": "insufficient_credits",
        "resolution_url": "https://x.test/topup",
        "retry_after_seconds": 3.5,
    }


def test_an_error_with_nothing_known_still_produces_a_complete_payload():
    """Every key must be present even when unset, so the reader can look them up
    without guarding each one."""
    payload = CaptchaKrakenAPIError("something went wrong").to_payload()
    assert set(payload["ck_error"]) == {
        "status", "code", "resolution_url", "retry_after_seconds",
    }
    assert all(v is None for v in payload["ck_error"].values())
