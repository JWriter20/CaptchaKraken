"""A PIN must read ITS OWN registry entry, not `latest`'s.

`_registry_default` looked the field up on the `latest` entry unconditionally,
so pinning `CAPTCHA_LORA_ADAPTER` moved the ADAPTER and nothing else. Every
other fact about the model — the base weights to load it onto, the served
`lora_name` to put in the request, the revision to pin — still came from
whichever model happened to be `latest`.

That was survivable while every registered model was a 9B on the same base and
answered to the same served name. It stopped being survivable the day the
registry gained `CaptchaKraken/Abyss-27B`, which is a **Qwen3.8-27B**: pinning
it downloaded a 9B base, tried to load a 27B adapter onto it, and sent
`captcha-v12` as the model name. Same shape for every expert arm, whose whole
purpose is a `lora_name` of its own.

Nothing about it errors in the client — it errors, if at all, deep inside vLLM
with a shape mismatch, or not at all if the endpoint happens to serve something
by that name. That is the mispairing models.json exists to prevent, on a third
axis after prompts and pixels.

An UNREGISTERED pin still falls back to `latest`, exactly as before. A
self-hoster's own adapter is not in our registry and never will be, and
changing what they resolve to would be a break with no benefit.
"""
import pytest

from captchakraken import config, prompts

PINS = [
    # (pinned adapter, expected base_model, expected lora_name)
    ("CaptchaKraken/Abyss-27B", "Qwen/Qwen3.8-27B", "abyss-27b"),
    ("CaptchaKraken/Abyss-grid", "Qwen/Qwen3.5-9B", "abyss-grid"),
    ("CaptchaKraken/Abyss-text", "Qwen/Qwen3.5-9B", "abyss-text"),
    ("CaptchaKraken/CaptchaKrakenV1_Lora",
     "RedHatAI/Qwen3.5-9B-FP8-dynamic", "captcha"),
]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in ("CAPTCHA_LORA_ADAPTER", "CAPTCHA_LORA_NAME", "CAPTCHA_BASE_MODEL",
                "CAPTCHA_LORA_REVISION"):
        monkeypatch.delenv(var, raising=False)
    prompts.clear_cache()


@pytest.mark.parametrize("pin, base, name", PINS)
def test_a_pinned_adapter_brings_its_own_base_and_served_name(monkeypatch, pin, base, name):
    # SOMEONE ELSE'S ENDPOINT, pinned explicitly.
    #
    # `lora_name()` answers differently against OUR API — there it returns the
    # hosted-only model, which is the whole point of `hosted_default`. Leaving
    # the endpoint unset does not mean "not hosted": `base_url()` falls through
    # to the credentials file, so on a machine that has ever signed in through
    # the MCP flow this resolved to api.captchakraken.com and the assertion
    # below became a different question than the one it is asking. Both halves
    # are now asserted, each against the endpoint it belongs to.
    monkeypatch.setenv("VLLM_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("CAPTCHA_LORA_ADAPTER", pin)
    assert config.lora_adapter() == pin
    assert config.base_model() == base
    assert config.lora_name() == name


def test_pinning_the_served_name_alone_resolves_the_same_entry():
    """`CAPTCHA_LORA_NAME` is the other pin, and a served alias is what a
    licence holder is actually given."""
    import os
    os.environ["CAPTCHA_LORA_NAME"] = "abyss-grid"
    try:
        assert config.base_model() == "Qwen/Qwen3.5-9B"
        assert config.lora_name() == "abyss-grid"    # the env pin always wins
    finally:
        del os.environ["CAPTCHA_LORA_NAME"]


def test_an_unpinned_client_still_resolves_latest(monkeypatch):
    """The default path must not have moved — for a self-hoster."""
    # SOMEONE ELSE'S ENDPOINT, pinned explicitly.
    #
    # `lora_name()` answers differently against OUR API — there it returns the
    # hosted-only model, which is the whole point of `hosted_default`. Leaving
    # the endpoint unset does not mean "not hosted": `base_url()` falls through
    # to the credentials file, so on a machine that has ever signed in through
    # the MCP flow this resolved to api.captchakraken.com and the assertion
    # below became a different question than the one it is asking. Both halves
    # are now asserted, each against the endpoint it belongs to.
    monkeypatch.setenv("VLLM_BASE_URL", "http://127.0.0.1:8000/v1")
    latest = prompts.latest_model()
    entry = prompts.registered_models()[latest]
    assert config.lora_adapter() == latest
    assert config.base_model() == entry["base_model"]
    assert config.lora_name() == entry["lora_name"]


def test_our_own_endpoint_gets_the_hosted_model_instead(monkeypatch):
    """The other half, and the reason the fence above exists.

    `hosted_default` is hosted-only: our API serves it and nobody can download
    it, so naming it at someone else's vLLM would be a 404 rather than an
    upgrade. Which endpoint is being dialled is therefore part of the answer,
    and a test that does not say which one is asking two questions at once.
    """
    monkeypatch.delenv("CAPTCHA_LORA_NAME", raising=False)
    monkeypatch.setenv("VLLM_BASE_URL", "https://api.captchakraken.com/v1")
    registry = prompts.registered_models()
    served = config.lora_name()
    assert served != registry[prompts.latest_model()]["lora_name"], (
        "against our own endpoint the client must ask for the hosted model, "
        "not the downloadable default")
    # THE ALIAS, not the entry's own arm: a routed mixture is several names and
    # only the alias routes. See `_hosted_default_name`.
    assert config.is_hosted_endpoint()


def test_an_unregistered_pin_still_falls_back_to_latest(monkeypatch):
    """A self-hoster's own adapter is not ours to have an opinion about, and
    changing what it resolves to would be a break with no benefit."""
    monkeypatch.setenv("CAPTCHA_LORA_ADAPTER", "some-stranger/their-own-lora")
    entry = prompts.registered_models()[prompts.latest_model()]
    assert config.lora_adapter() == "some-stranger/their-own-lora"
    assert config.base_model() == entry["base_model"]


def test_an_explicit_env_override_still_wins(monkeypatch):
    """Pinning is opt-in and always wins — including over the entry's own field."""
    monkeypatch.setenv("CAPTCHA_LORA_ADAPTER", "CaptchaKraken/Abyss-27B")
    monkeypatch.setenv("CAPTCHA_BASE_MODEL", "Qwen/Qwen3.5-9B")
    assert config.base_model() == "Qwen/Qwen3.5-9B"
