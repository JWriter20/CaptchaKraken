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
