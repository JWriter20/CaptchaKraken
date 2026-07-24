"""Tests for the fleet-routing and hosted-API request headers.

`routing_headers` turns CAPTCHA_REQUEST_PRIORITY into the `X-JH-Priority` header
that the fleet's haproxy front routes on (values >5 → backup GPUs). The whole
point is that it fires ONLY when deliberately set — an unset or malformed value
must never silently tag production traffic for the backups — so that boundary is
what these pin. Hermetic: no server, no network.

It also emits the two hosted-API headers: `X-CK-Client` (which integration made
the request, used to attribute camoufox revenue) and `X-CK-Session` (groups the
rounds of one captcha into a single billable attempt). Both carry money
implications, so the tests below pin that they are absent unless set, survive
independently of a malformed priority, and can never inject extra headers.
"""
from captchakraken.planner import (
    _CLIENT_HEADER,
    _PRIORITY_HEADER,
    _SESSION_HEADER,
    routing_headers,
)


def test_no_priority_env_yields_no_header():
    assert routing_headers(env={}) == {}


def test_empty_or_whitespace_value_yields_no_header():
    assert routing_headers(env={"CAPTCHA_REQUEST_PRIORITY": ""}) == {}
    assert routing_headers(env={"CAPTCHA_REQUEST_PRIORITY": "   "}) == {}


def test_non_integer_value_is_ignored_not_forwarded():
    # A typo must not tag traffic — better no routing than wrong routing.
    assert routing_headers(env={"CAPTCHA_REQUEST_PRIORITY": "low"}) == {}
    assert routing_headers(env={"CAPTCHA_REQUEST_PRIORITY": "5.5"}) == {}


def test_integer_value_becomes_the_header():
    assert routing_headers(env={"CAPTCHA_REQUEST_PRIORITY": "10"}) == {_PRIORITY_HEADER: "10"}


def test_value_is_normalized_to_a_bare_int_string():
    # haproxy compares it as an integer (req.hdr_val), so surrounding whitespace
    # or a leading zero must not reach the wire as-is.
    assert routing_headers(env={"CAPTCHA_REQUEST_PRIORITY": " 07 "}) == {_PRIORITY_HEADER: "7"}


def test_the_tier2_default_of_10_clears_the_routing_threshold():
    # tier2_gate.sh defaults CAPTCHA_REQUEST_PRIORITY=10; haproxy routes >5 to the
    # backups, so 10 must survive as an int well above the threshold.
    hdr = routing_headers(env={"CAPTCHA_REQUEST_PRIORITY": "10"})
    assert int(hdr[_PRIORITY_HEADER]) > 5


# ── Hosted-API headers ──────────────────────────────────────────────────────


def test_self_hosted_users_send_neither_hosted_header():
    # The default path must stay byte-identical for self-hosters.
    assert routing_headers(env={}) == {}


def test_client_and_session_are_forwarded_when_set():
    hdrs = routing_headers(
        env={
            "CAPTCHA_KRAKEN_CLIENT": "camoufox/0.4.11",
            "CAPTCHA_KRAKEN_SESSION": "6f1a2b3c-0000-4000-8000-000000000001",
        }
    )
    assert hdrs[_CLIENT_HEADER] == "camoufox/0.4.11"
    assert hdrs[_SESSION_HEADER] == "6f1a2b3c-0000-4000-8000-000000000001"


def test_blank_values_are_dropped_rather_than_sent_empty():
    # An empty header would read as "attributed to nothing" downstream; absent is
    # unambiguous.
    assert routing_headers(env={"CAPTCHA_KRAKEN_CLIENT": "   "}) == {}
    assert routing_headers(env={"CAPTCHA_KRAKEN_SESSION": ""}) == {}


def test_a_malformed_priority_does_not_suppress_attribution():
    # Regression guard: the headers are derived independently. If a typo'd
    # priority swallowed the client header, camoufox traffic would silently be
    # counted as direct and understate the partner's revenue share.
    hdrs = routing_headers(
        env={"CAPTCHA_REQUEST_PRIORITY": "oops", "CAPTCHA_KRAKEN_CLIENT": "camoufox/1.0"}
    )
    assert hdrs == {_CLIENT_HEADER: "camoufox/1.0"}


def test_crlf_cannot_inject_additional_headers():
    # These values come from the environment and reach the wire verbatim, so a
    # newline must never be able to splice in another header.
    hdrs = routing_headers(
        env={"CAPTCHA_KRAKEN_CLIENT": "camoufox\r\nX-CK-Session: forged"}
    )
    assert hdrs == {_CLIENT_HEADER: "camoufoxX-CK-Session: forged"}
    assert "\r" not in hdrs[_CLIENT_HEADER] and "\n" not in hdrs[_CLIENT_HEADER]
    assert _SESSION_HEADER not in hdrs


def test_oversized_value_is_truncated_not_dropped():
    hdrs = routing_headers(env={"CAPTCHA_KRAKEN_CLIENT": "c" * 500})
    assert len(hdrs[_CLIENT_HEADER]) == 128
