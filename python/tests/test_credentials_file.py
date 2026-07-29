"""Tests for the hosted-API credentials-file fallback in `config.api_key()`.

The MCP signup flow writes the account key to a 0600 file under the state dir
instead of returning it through the agent transcript, so the key never lands in
an LLM context window. `api_key()` reading that file is what lets a solve
authenticate with NO env vars set at all.

Two boundaries carry real consequences and are pinned here:
  * env must still win, or an explicit override would be silently ignored;
  * a missing/garbage file must degrade to "EMPTY" exactly as before, or every
    self-hosted user without the file breaks.

Hermetic: no network, no server. `CAPTCHA_KRAKEN_STATE_DIR` is redirected at a
tmp_path so a developer's real ~/.captchakraken is never read or written.
"""
import pytest

from captchakraken import config


@pytest.fixture
def creds(tmp_path, monkeypatch):
    """Redirect the state dir to tmp and clear both key env vars.

    Returns a writer so each test states only the file contents it cares about.
    """
    monkeypatch.setenv("CAPTCHA_KRAKEN_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("CAPTCHA_KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    def write(text: str):
        (tmp_path / "credentials").write_text(text, encoding="utf-8")

    return write


def test_no_file_still_yields_empty(creds):
    # The self-hosted default. Must not raise, must not change behaviour.
    assert config.api_key() == "EMPTY"


def test_bare_token_is_read(creds):
    creds("ck_live_abc123\n")
    assert config.api_key() == "ck_live_abc123"


def test_env_file_form_is_read(creds):
    # The MCP server may write a sourceable env file rather than a bare token.
    creds("CAPTCHA_KRAKEN_API_KEY=ck_live_abc123\n")
    assert config.api_key() == "ck_live_abc123"


def test_quotes_and_comments_are_tolerated(creds):
    creds('# written by @captchakraken/mcp\n\nCAPTCHA_KRAKEN_API_KEY="ck_live_xyz"\n')
    assert config.api_key() == "ck_live_xyz"


def test_unrelated_env_keys_do_not_yield_a_bogus_token(creds):
    # Picking up the RHS of some unrelated line would send garbage as a bearer
    # token and surface as a confusing 401.
    creds("VLLM_BASE_URL=https://api.captchakraken.com/v1\n")
    assert config.api_key() == "EMPTY"


def test_explicit_env_wins_over_the_file(creds, monkeypatch):
    creds("ck_live_from_file\n")
    monkeypatch.setenv("CAPTCHA_KRAKEN_API_KEY", "ck_live_from_env")
    assert config.api_key() == "ck_live_from_env"


def test_vllm_api_key_still_wins_over_the_file(creds, monkeypatch):
    # Pre-existing precedence must not regress for self-hosters.
    creds("ck_live_from_file\n")
    monkeypatch.setenv("VLLM_API_KEY", "local-server-key")
    assert config.api_key() == "local-server-key"


def test_unreadable_file_degrades_to_empty(creds, tmp_path):
    # A directory where the file should be is the cheap portable stand-in for
    # "open() raises". Must not propagate an exception into the solve path.
    (tmp_path / "credentials").mkdir()
    assert config.api_key() == "EMPTY"


def test_credentials_path_follows_the_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTCHA_KRAKEN_STATE_DIR", str(tmp_path))
    assert config.credentials_path() == tmp_path / "credentials"


# ── The endpoint travels with the key ───────────────────────────────────────
#
# A key without an endpoint is useless to a hosted user: they authenticate
# perfectly against localhost, where nothing is listening. So the MCP writes
# both, and these pin that reading the pair back cannot redirect a self-hoster.


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    """As `creds`, but also clears VLLM_BASE_URL so the file is what decides."""
    monkeypatch.setenv("CAPTCHA_KRAKEN_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("VLLM_BASE_URL", raising=False)
    monkeypatch.delenv("CAPTCHA_KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    def write(text: str):
        (tmp_path / "credentials").write_text(text, encoding="utf-8")

    return write


def test_no_file_still_defaults_to_localhost(endpoint):
    # The self-hosting default. Changing this would break every existing user
    # who runs their own vLLM and has never seen a credentials file.
    assert config.base_url().startswith("http://localhost:")


def test_file_supplies_the_hosted_endpoint(endpoint):
    endpoint(
        "CAPTCHA_KRAKEN_API_KEY=ck_live_abc\n"
        "VLLM_BASE_URL=https://api.captchakraken.com/v1\n"
    )
    assert config.base_url() == "https://api.captchakraken.com/v1"
    # …and the key still resolves from the same file, unchanged.
    assert config.api_key() == "ck_live_abc"


def test_the_product_spelling_of_the_base_url_is_accepted_too(endpoint):
    endpoint("CAPTCHA_KRAKEN_BASE_URL=https://api.captchakraken.com/v1\n")
    assert config.base_url() == "https://api.captchakraken.com/v1"


def test_explicit_env_base_url_wins_over_the_file(endpoint, monkeypatch):
    # Without this, pointing the dev environment at a staging endpoint would be
    # silently ignored whenever a credentials file happened to exist.
    endpoint("VLLM_BASE_URL=https://api.captchakraken.com/v1\n")
    monkeypatch.setenv("VLLM_BASE_URL", "http://localhost:9999/v1")
    assert config.base_url() == "http://localhost:9999/v1"


def test_a_bare_token_file_does_not_redirect_anyone(endpoint):
    # A bare token carries no endpoint. Inferring the hosted one from "there is
    # a file" would hijack a self-hoster who hand-wrote their local key here.
    endpoint("ck_live_abc123\n")
    assert config.api_key() == "ck_live_abc123"
    assert config.base_url().startswith("http://localhost:")


def test_quotes_and_comments_are_tolerated_on_the_url_too(endpoint):
    endpoint('# written by captchakraken-mcp\n\nVLLM_BASE_URL="https://api.captchakraken.com/v1"\n')
    assert config.base_url() == "https://api.captchakraken.com/v1"


def test_unreadable_file_still_defaults_to_localhost(endpoint, tmp_path):
    (tmp_path / "credentials").mkdir()
    assert config.base_url().startswith("http://localhost:")


def test_a_later_bare_line_cannot_clobber_the_real_credential(endpoint):
    # Hand-edited files pick up stray prose. The first bare line is the token;
    # a trailing note must not replace it with garbage.
    endpoint("ck_live_real\nsome stray note\n")
    assert config.api_key() == "ck_live_real"
