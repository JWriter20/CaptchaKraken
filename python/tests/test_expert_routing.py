"""`text` is left out of the fixture map on purpose: the day that family reached ckgate without a marker, refusing it cost every distorted-text solve in production for a day. An unknown pin raises; arms are never public; the routed models are spelled out so a second one needs an edit."""

import json
import os
from pathlib import Path

import pytest

from captchakraken import prompts


ROUTED = {
    "latest": "Acme/Plain",
    "models": {
        "Acme/Plain": {"prompt_version": "2", "lora_name": "plain"},
        "Acme/Routed": {
            "prompt_version": "2",
            "lora_name": "routed",
            "pixel_budget": {"min": 518400, "max": 518400},
            "experts": {"grid": "routed-grid", "pixel": "routed-general",
                        "video": "routed-video"},
        },
    },
    "served_aliases": {
        "routed": "Acme/Routed",
        "routed-grid": "Acme/Routed",
        "routed-general": "Acme/Routed",
        "routed-video": "Acme/Routed",
    },
}


@pytest.fixture
def routed(monkeypatch):
    prompts.clear_cache()
    monkeypatch.setattr(prompts, "_load_registry", lambda: json.loads(json.dumps(ROUTED)))
    yield
    prompts.clear_cache()


def test_each_family_reaches_its_own_expert(routed):
    assert prompts.route("routed", "grid") == "routed-grid"
    assert prompts.route("routed", "pixel") == "routed-general"
    assert prompts.route("routed", "video") == "routed-video"


def test_an_unmapped_family_falls_back_to_the_generalist_not_an_error(routed):
    assert prompts.route("routed", "text") == "routed"


def test_an_unrecognised_family_falls_back_too(routed):
    for family in (None, "", "something-new"):
        assert prompts.route("routed", family) == "routed"


def test_the_expert_name_resolves_to_the_same_prompts_and_band(routed):
    for name in ("routed", "routed-grid", "routed-general", "routed-video"):
        assert prompts.canonical_model_id(name) == "Acme/Routed"
        assert prompts.resolve(name).version == "2"
        assert prompts.pixel_budget(name).minimum == 518400


def test_an_unknown_family_key_in_the_registry_is_dropped(routed, monkeypatch):
    bad = json.loads(json.dumps(ROUTED))
    bad["models"]["Acme/Routed"]["experts"]["gird"] = "routed-typo"
    prompts.clear_cache()
    monkeypatch.setattr(prompts, "_load_registry", lambda: bad)
    assert "gird" not in prompts.experts("routed")
    assert prompts.route("routed", "gird") == "routed"


def test_a_pin_overrides_the_family(routed):
    assert prompts.route("routed", "video", pin="grid") == "routed-grid"


def test_an_unknown_pin_raises(routed):
    with pytest.raises(ValueError, match="unknown expert"):
        prompts.route("routed", "grid", pin="gird")


def test_a_pin_against_an_unrouted_model_raises(routed):
    with pytest.raises(ValueError, match="declares no experts"):
        prompts.route("plain", "grid", pin="grid")


def test_the_env_pin_is_read_and_an_empty_one_is_unset(monkeypatch):
    monkeypatch.delenv(prompts.EXPERT_ENV, raising=False)
    assert prompts.expert_pin() is None
    monkeypatch.setenv(prompts.EXPERT_ENV, "  ")
    assert prompts.expert_pin() is None
    monkeypatch.setenv(prompts.EXPERT_ENV, " grid ")
    assert prompts.expert_pin() == "grid"


ROUTED_IN_REGISTRY = {"CaptchaKraken/Abyss"}


def test_only_the_declared_routed_models_are_routed():
    prompts.clear_cache()
    routed = {r for r in prompts.registered_models() if prompts.experts(r)}
    assert routed == ROUTED_IN_REGISTRY


def test_abyss_routes_all_four_families_to_distinct_experts():
    prompts.clear_cache()
    mapping = prompts.experts("abyss")
    assert set(mapping) == set(prompts.PROMPT_FAMILIES)
    assert len(set(mapping.values())) == 4
    for name in mapping.values():
        assert prompts.canonical_model_id(name) in prompts.registered_models()
        assert prompts.resolve(name).version == "2"
        assert prompts.pixel_budget(name).minimum == 518400


def test_no_expert_arm_is_public():
    prompts.clear_cache()
    assert prompts.is_licensed("abyss"), "the router itself must not be public"
    for name in prompts.experts("abyss").values():
        assert prompts.availability(name) != prompts.PUBLIC, f"{name} is PUBLIC"


def test_an_unrouted_model_returns_the_name_it_was_given():
    prompts.clear_cache()
    for name in ("captcha", "captcha-v12", "not-registered-at-all"):
        for family in ("grid", "pixel", "video", "text", None):
            assert prompts.route(name, family) == name


_GATE = Path(os.environ.get("CAPTCHA_PARITY_GATE") or "/nonexistent")


@pytest.mark.skipif(not _GATE.is_file(),
                    reason="set CAPTCHA_PARITY_GATE to check the release gate's copy")
def test_prompt_families_match_the_release_gate():
    import ast
    tree = ast.parse(_GATE.read_text(encoding="utf-8"))
    found = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "PROMPT_FAMILIES" for t in node.targets):
            found = ast.literal_eval(node.value)
    assert found is not None, "the gate lost PROMPT_FAMILIES"
    assert tuple(found) == tuple(prompts.PROMPT_FAMILIES)
