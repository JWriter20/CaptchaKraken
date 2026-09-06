"""A ROUTED model picks its adapter per prompt family; nothing else changes.

Three properties, and the third is the one that matters most:

1. A routed model sends a DIFFERENT adapter name per family, chosen by the
   family the request is about to prompt in — the router is the prompt family
   and it needs no help from the caller (docs/MOE_LORA_DESIGN.md §11).
2. A pin overrides that, and a pin that cannot mean anything RAISES rather than
   quietly measuring the generalist and reporting it as the expert.
3. EVERY MODEL PUBLISHED SO FAR IS UNROUTED, and for those the bytes on the
   wire are identical to what they were before this mechanism existed. That is
   the whole backwards-compatibility claim, so it is asserted against the real
   shipped registry rather than a fixture.
"""
import json
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


# ── 1. routing by family ────────────────────────────────────────────────────

def test_each_family_reaches_its_own_expert(routed):
    assert prompts.route("routed", "grid") == "routed-grid"
    assert prompts.route("routed", "pixel") == "routed-general"
    assert prompts.route("routed", "video") == "routed-video"


def test_an_unmapped_family_falls_back_to_the_generalist_not_an_error(routed):
    """`text` is deliberately absent from the fixture's map.

    The day the generation-2 `text` family reached ckgate without a marker,
    refusing it cost every distorted-text solve in production for a day. A
    family with no expert is answered by the model the caller named.
    """
    assert prompts.route("routed", "text") == "routed"


def test_an_unrecognised_family_falls_back_too(routed):
    for family in (None, "", "something-new"):
        assert prompts.route("routed", family) == "routed"


def test_the_expert_name_resolves_to_the_same_prompts_and_band(routed):
    """The name that goes ON THE WIRE has to resolve, or every routed solve
    resolves its prompts by guessing."""
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
    # and the typo never reaches the wire
    assert prompts.route("routed", "gird") == "routed"


# ── 2. the pin ──────────────────────────────────────────────────────────────

def test_a_pin_overrides_the_family(routed):
    assert prompts.route("routed", "video", pin="grid") == "routed-grid"


def test_an_unknown_pin_raises(routed):
    with pytest.raises(ValueError, match="unknown expert"):
        prompts.route("routed", "grid", pin="gird")


def test_a_pin_against_an_unrouted_model_raises(routed):
    """Silently ignoring it would report the generalist's score as an expert's."""
    with pytest.raises(ValueError, match="declares no experts"):
        prompts.route("plain", "grid", pin="grid")


def test_the_env_pin_is_read_and_an_empty_one_is_unset(monkeypatch):
    monkeypatch.delenv(prompts.EXPERT_ENV, raising=False)
    assert prompts.expert_pin() is None
    monkeypatch.setenv(prompts.EXPERT_ENV, "  ")
    assert prompts.expert_pin() is None
    monkeypatch.setenv(prompts.EXPERT_ENV, " grid ")
    assert prompts.expert_pin() == "grid"


# ── 3. the shipped registry is unrouted, and stays byte-identical ───────────

#: The one routed model in the shipped registry. Spelled out so that a SECOND
#: one cannot appear without someone editing this line — registering experts is
#: part of publishing a routed model, and the publish commit is where the arms'
#: prompt generation and pixel band get confirmed against their run records.
ROUTED_IN_REGISTRY = {"CaptchaKraken/Abyss"}


def test_only_the_declared_routed_models_are_routed():
    prompts.clear_cache()
    routed = {r for r in prompts.registered_models() if prompts.experts(r)}
    assert routed == ROUTED_IN_REGISTRY


def test_abyss_routes_all_four_families_to_distinct_experts():
    """Four families, four names, and every one of them resolvable — that last
    part is what `check_prompt_parity.py` gates and what keeps a routed solve
    from resolving its prompts by guessing."""
    prompts.clear_cache()
    mapping = prompts.experts("abyss")
    assert set(mapping) == set(prompts.PROMPT_FAMILIES)
    assert len(set(mapping.values())) == 4
    for name in mapping.values():
        assert prompts.canonical_model_id(name) == "CaptchaKraken/Abyss"
        assert prompts.resolve(name).version == "2"
        assert prompts.pixel_budget(name).minimum == 518400


def test_abyss_is_still_licensed_now_that_it_is_a_9b():
    """The base model moved from the 27B to Qwen3.5-9B, which is what every
    PUBLISHED model is built on. `availability` is what refuses the weights,
    and it must not have travelled with the base."""
    prompts.clear_cache()
    assert prompts.is_licensed("abyss")
    for name in prompts.experts("abyss").values():
        assert prompts.is_licensed(name), f"{name} is downloadable"


def test_an_unrouted_model_returns_the_name_it_was_given():
    prompts.clear_cache()
    for name in ("captcha", "captcha-v12", "not-registered-at-all"):
        for family in ("grid", "pixel", "video", "text", None):
            assert prompts.route(name, family) == name


def test_prompt_families_match_the_release_gate():
    """Two copies, because the gate reads the client by AST and cannot import
    it. Same reason AVAILABILITIES is spelled out twice."""
    import ast
    import pathlib
    gate = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "check_prompt_parity.py"
    tree = ast.parse(gate.read_text(encoding="utf-8"))
    found = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "PROMPT_FAMILIES" for t in node.targets):
            found = ast.literal_eval(node.value)
    assert found is not None, "the gate lost PROMPT_FAMILIES"
    assert tuple(found) == tuple(prompts.PROMPT_FAMILIES)
