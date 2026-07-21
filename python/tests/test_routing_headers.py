"""Tests for the fleet-routing priority header.

`routing_headers` turns CAPTCHA_REQUEST_PRIORITY into the `X-JH-Priority` header
that the fleet's haproxy front routes on (values >5 → backup GPUs). The whole
point is that it fires ONLY when deliberately set — an unset or malformed value
must never silently tag production traffic for the backups — so that boundary is
what these pin. Hermetic: no server, no network.
"""
from captchakraken.planner import _PRIORITY_HEADER, routing_headers


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
