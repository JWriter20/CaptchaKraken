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
    assert routing_headers(env={"CAPTCHA_REQUEST_PRIORITY": "low"}) == {}
    assert routing_headers(env={"CAPTCHA_REQUEST_PRIORITY": "5.5"}) == {}


def test_integer_value_becomes_the_header():
    assert routing_headers(env={"CAPTCHA_REQUEST_PRIORITY": "10"}) == {_PRIORITY_HEADER: "10"}


def test_value_is_normalized_to_a_bare_int_string():
    assert routing_headers(env={"CAPTCHA_REQUEST_PRIORITY": " 07 "}) == {_PRIORITY_HEADER: "7"}


def test_the_tier2_default_of_10_clears_the_routing_threshold():
    hdr = routing_headers(env={"CAPTCHA_REQUEST_PRIORITY": "10"})
    assert int(hdr[_PRIORITY_HEADER]) > 5


def test_self_hosted_users_send_neither_hosted_header():
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
    assert routing_headers(env={"CAPTCHA_KRAKEN_CLIENT": "   "}) == {}
    assert routing_headers(env={"CAPTCHA_KRAKEN_SESSION": ""}) == {}


def test_a_malformed_priority_does_not_suppress_attribution():
    hdrs = routing_headers(
        env={"CAPTCHA_REQUEST_PRIORITY": "oops", "CAPTCHA_KRAKEN_CLIENT": "camoufox/1.0"}
    )
    assert hdrs == {_CLIENT_HEADER: "camoufox/1.0"}


def test_crlf_cannot_inject_additional_headers():
    hdrs = routing_headers(
        env={"CAPTCHA_KRAKEN_CLIENT": "camoufox\r\nX-CK-Session: forged"}
    )
    assert hdrs == {_CLIENT_HEADER: "camoufoxX-CK-Session: forged"}
    assert "\r" not in hdrs[_CLIENT_HEADER] and "\n" not in hdrs[_CLIENT_HEADER]
    assert _SESSION_HEADER not in hdrs


def test_oversized_value_is_truncated_not_dropped():
    hdrs = routing_headers(env={"CAPTCHA_KRAKEN_CLIENT": "c" * 500})
    assert len(hdrs[_CLIENT_HEADER]) == 128


from captchakraken.planner import _EXTRA_HEADERS_ENV


def _extra(value):
    return routing_headers(env={_EXTRA_HEADERS_ENV: value})


def test_absent_and_empty_add_nothing():
    assert routing_headers(env={}) == {}
    assert _extra("") == {}
    assert _extra("   \n  ") == {}


def test_single_pair():
    assert _extra("X-CK-Dev-Auth: hunter2") == {"X-CK-Dev-Auth": "hunter2"}


def test_whitespace_around_the_pair_is_ignored():
    assert _extra("  X-CK-Dev-Auth :  hunter2  ") == {"X-CK-Dev-Auth": "hunter2"}


def test_several_pairs_by_newline_and_comma():
    assert _extra("X-One: a\nX-Two: b") == {"X-One": "a", "X-Two": "b"}
    assert _extra("X-One: a, X-Two: b") == {"X-One": "a", "X-Two": "b"}


def test_a_value_may_contain_a_colon():
    assert _extra("X-Proxy: Basic abc:def") == {"X-Proxy": "Basic abc:def"}


def test_entries_without_a_colon_are_dropped_not_guessed():
    assert _extra("nonsense") == {}
    assert _extra("X-Good: yes\nnonsense") == {"X-Good": "yes"}


def test_empty_name_or_value_is_dropped():
    assert _extra(": value") == {}
    assert _extra("X-Empty:") == {}
    assert _extra("X-Empty:    ") == {}


def test_crlf_cannot_splice_another_header():
    out = _extra("X-Bad: a\r\nX-Injected: b")
    assert out == {"X-Bad": "a", "X-Injected": "b"}
    for value in out.values():
        assert "\r" not in value and "\n" not in value


def test_invalid_header_names_are_dropped():
    assert _extra("Bad Name: v") == {}
    assert _extra("X-Bad\tName: v") == {}


def test_protected_headers_cannot_be_overwritten():
    assert _extra("Authorization: Bearer stolen") == {}
    assert _extra("authorization: Bearer stolen") == {}
    assert _extra("Content-Type: text/plain") == {}
    assert _extra("X-CK-Client: not-camoufox") == {}
    assert _extra("X-CK-Session: pinned") == {}


def test_protected_names_do_not_block_the_rest_of_the_line():
    assert _extra("Authorization: nope\nX-Fine: yes") == {"X-Fine": "yes"}


def test_extra_headers_compose_with_the_others():
    out = routing_headers(
        env={
            "CAPTCHA_KRAKEN_CLIENT": "camoufox/0.4.11",
            _EXTRA_HEADERS_ENV: "X-CK-Dev-Auth: hunter2",
        }
    )
    assert out == {"X-CK-Client": "camoufox/0.4.11", "X-CK-Dev-Auth": "hunter2"}
