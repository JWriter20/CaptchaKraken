"""A licensed model is refused in plan(), before any download, so --dry-run shows it; the alternative is a RepositoryNotFoundError that reads as "you are not logged in". --engine-only is unguarded on purpose."""

import pytest

from captchakraken import prompts, updater

LICENSED_REPO = "CaptchaKraken/Abyss"
LICENSED_SERVED = "abyss"
PUBLIC_REPO = "CaptchaKraken/CaptchaKraken-Lora-v1.2"


def test_the_licensed_model_is_registered_as_licensed():
    assert prompts.availability(LICENSED_REPO) == prompts.LICENSED
    assert prompts.is_licensed(LICENSED_REPO)


def test_a_published_model_is_public():
    assert prompts.availability(PUBLIC_REPO) == prompts.PUBLIC
    assert not prompts.is_licensed(PUBLIC_REPO)


def test_an_unregistered_model_is_public():
    assert not prompts.is_licensed("some-stranger/their-own-lora")


def test_an_unrecognised_availability_reads_as_licensed(monkeypatch):
    monkeypatch.setattr(prompts, "_REGISTRY", {
        "models": {"org/m": {"availability": "licenced"}}})
    assert prompts.is_licensed("org/m")


def test_the_served_name_resolves_to_the_licensed_entry():
    assert prompts.canonical_model_id(LICENSED_SERVED) == LICENSED_REPO


def test_the_served_name_carries_its_own_prompt_generation():
    assert prompts.resolve(LICENSED_SERVED) == prompts.resolve(LICENSED_REPO)
    assert prompts.resolve(LICENSED_SERVED).source == f"registry:{LICENSED_REPO}"


def test_the_served_name_is_licensed_too():
    assert prompts.is_licensed(LICENSED_SERVED)


def test_fetch_refuses_a_licensed_adapter():
    with pytest.raises(updater.LicensedModelError) as exc:
        updater.plan(lora=LICENSED_REPO)
    message = str(exc.value)
    assert "api.captchakraken.com" in message
    assert "licence" in message


def test_fetch_refuses_the_served_name_as_well():
    with pytest.raises(updater.LicensedModelError):
        updater.plan(lora=LICENSED_SERVED)


def test_fetch_refuses_a_licensed_base_model():
    with pytest.raises(updater.LicensedModelError):
        updater.plan(base=LICENSED_REPO)


def test_the_refusal_happens_in_plan_so_dry_run_shows_it():
    with pytest.raises(updater.LicensedModelError):
        updater.plan(lora=LICENSED_REPO, engine=False)


def test_skipping_the_weights_skips_the_refusal():
    assert updater.plan(weights=False, lora=LICENSED_REPO)["downloads"] == []


def test_a_public_model_still_fetches():
    assert updater.plan(lora=PUBLIC_REPO)["lora_adapter"] == PUBLIC_REPO
