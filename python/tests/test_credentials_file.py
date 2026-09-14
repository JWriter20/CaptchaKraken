import pytest

from captchakraken import config


@pytest.fixture
def creds(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTCHA_KRAKEN_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("CAPTCHA_KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    def write(text: str):
        (tmp_path / "credentials").write_text(text, encoding="utf-8")

    return write


def test_no_file_still_yields_empty(creds):
    assert config.api_key() == "EMPTY"


def test_bare_token_is_read(creds):
    creds("ck_live_abc123\n")
    assert config.api_key() == "ck_live_abc123"


def test_env_file_form_is_read(creds):
    creds("CAPTCHA_KRAKEN_API_KEY=ck_live_abc123\n")
    assert config.api_key() == "ck_live_abc123"


def test_quotes_and_comments_are_tolerated(creds):
    creds('# written by @captchakraken/mcp\n\nCAPTCHA_KRAKEN_API_KEY="ck_live_xyz"\n')
    assert config.api_key() == "ck_live_xyz"


def test_unrelated_env_keys_do_not_yield_a_bogus_token(creds):
    creds("VLLM_BASE_URL=https://api.captchakraken.com/v1\n")
    assert config.api_key() == "EMPTY"


def test_explicit_env_wins_over_the_file(creds, monkeypatch):
    creds("ck_live_from_file\n")
    monkeypatch.setenv("CAPTCHA_KRAKEN_API_KEY", "ck_live_from_env")
    assert config.api_key() == "ck_live_from_env"


def test_vllm_api_key_still_wins_over_the_file(creds, monkeypatch):
    creds("ck_live_from_file\n")
    monkeypatch.setenv("VLLM_API_KEY", "local-server-key")
    assert config.api_key() == "local-server-key"


def test_unreadable_file_degrades_to_empty(creds, tmp_path):
    (tmp_path / "credentials").mkdir()
    assert config.api_key() == "EMPTY"


def test_credentials_path_follows_the_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTCHA_KRAKEN_STATE_DIR", str(tmp_path))
    assert config.credentials_path() == tmp_path / "credentials"


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPTCHA_KRAKEN_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("VLLM_BASE_URL", raising=False)
    monkeypatch.delenv("CAPTCHA_KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)

    def write(text: str):
        (tmp_path / "credentials").write_text(text, encoding="utf-8")

    return write


def test_no_file_still_defaults_to_localhost(endpoint):
    assert config.base_url().startswith("http://localhost:")


def test_file_supplies_the_hosted_endpoint(endpoint):
    endpoint(
        "CAPTCHA_KRAKEN_API_KEY=ck_live_abc\n"
        "VLLM_BASE_URL=https://api.captchakraken.com/v1\n"
    )
    assert config.base_url() == "https://api.captchakraken.com/v1"
    assert config.api_key() == "ck_live_abc"


def test_the_product_spelling_of_the_base_url_is_accepted_too(endpoint):
    endpoint("CAPTCHA_KRAKEN_BASE_URL=https://api.captchakraken.com/v1\n")
    assert config.base_url() == "https://api.captchakraken.com/v1"


def test_explicit_env_base_url_wins_over_the_file(endpoint, monkeypatch):
    endpoint("VLLM_BASE_URL=https://api.captchakraken.com/v1\n")
    monkeypatch.setenv("VLLM_BASE_URL", "http://localhost:9999/v1")
    assert config.base_url() == "http://localhost:9999/v1"


def test_a_bare_token_file_does_not_redirect_anyone(endpoint):
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
    endpoint("ck_live_real\nsome stray note\n")
    assert config.api_key() == "ck_live_real"
