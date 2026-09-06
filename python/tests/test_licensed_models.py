"""A licensed model is named, resolved and refused — never quietly approximated.

`availability: "licensed"` in models.json says the weights are not on the Hub:
the model is reachable through the hosted API or a licence, and by no other
route. Two client-side consequences follow, and this file pins both.

**Fetching it must refuse, not 404.** Without the refusal the failure is
huggingface_hub's `RepositoryNotFoundError`, which reads as "you are not logged
in" or "typo" — so the next thing a self-hoster does is hunt for a token that
will never exist. `CAPTCHA_LORA_ADAPTER` takes any string, which makes it the
one place a licensed name is plausibly typed by accident.

**Naming it must resolve its prompts from its own entry.** This is the half
that was missing: the gateway lets a licensed account send `model: "abyss"` and
lists the name to them on /v1/models precisely so their SDK can, but the client
had no `served_aliases` entry for it — so `canonical_model_id("abyss")` was
None and prompt resolution fell through to the `latest` branch. That is the
mispairing models.json exists to prevent (CLAUDE.md §3e), and it is silent: it
happens to land on the same generation today only because `latest` is on 2, and
would become wrong the moment either side moves without the other.
"""
import pytest

from captchakraken import prompts, updater

LICENSED_REPO = "CaptchaKraken/Abyss"
LICENSED_SERVED = "abyss"
PUBLIC_REPO = "CaptchaKraken/CaptchaKraken-Lora-v1.2"


# ── the registry states it ──────────────────────────────────────────────────

def test_the_licensed_model_is_registered_as_licensed():
    """Everything below keys off this one field."""
    assert prompts.availability(LICENSED_REPO) == prompts.LICENSED
    assert prompts.is_licensed(LICENSED_REPO)


def test_a_published_model_is_public():
    """A guard that refuses everything is a guard that gets deleted."""
    assert prompts.availability(PUBLIC_REPO) == prompts.PUBLIC
    assert not prompts.is_licensed(PUBLIC_REPO)


def test_an_unregistered_model_is_public():
    """Not ours, so not ours to refuse — a self-hoster's own adapter must fetch."""
    assert not prompts.is_licensed("some-stranger/their-own-lora")


def test_an_unrecognised_availability_reads_as_licensed(monkeypatch):
    """Fails CLOSED. A registry that says "licenced" must not hand out a
    proprietary model over a spelling; the release gate rejects the typo."""
    monkeypatch.setattr(prompts, "_REGISTRY", {
        "models": {"org/m": {"availability": "licenced"}}})
    assert prompts.is_licensed("org/m")


# ── the served name is the name a licence holder actually sends ─────────────

def test_the_served_name_resolves_to_the_licensed_entry():
    """`abyss` is what the caller writes; the gateway lists it to them.

    Without the alias this is None, and every lookup below silently answers for
    a model the registry has never heard of.
    """
    assert prompts.canonical_model_id(LICENSED_SERVED) == LICENSED_REPO


def test_the_served_name_carries_its_own_prompt_generation():
    """The mispairing models.json exists to prevent.

    Asserted as "the same PromptSet the repo id gives", not as a version
    number: the point is that both spellings of one model answer identically,
    which stays true when the model retrains onto a new generation.

    `source` is asserted too, because equality alone would also hold if BOTH
    spellings fell through to the `latest` fallback — which is the bug, not the
    fix, and today's `latest` happens to share the generation.
    """
    assert prompts.resolve(LICENSED_SERVED) == prompts.resolve(LICENSED_REPO)
    assert prompts.resolve(LICENSED_SERVED).source == f"registry:{LICENSED_REPO}"


def test_the_served_name_is_licensed_too():
    """`CAPTCHA_LORA_ADAPTER=abyss` is at least as likely a typo as the repo id."""
    assert prompts.is_licensed(LICENSED_SERVED)


# ── fetching it refuses, and says what to do instead ────────────────────────

def test_fetch_refuses_a_licensed_adapter():
    with pytest.raises(updater.LicensedModelError) as exc:
        updater.plan(lora=LICENSED_REPO)
    message = str(exc.value)
    # Must say what to do instead, not merely no.
    assert "api.captchakraken.com" in message
    assert "licence" in message


def test_fetch_refuses_the_served_name_as_well():
    with pytest.raises(updater.LicensedModelError):
        updater.plan(lora=LICENSED_SERVED)


def test_fetch_refuses_a_licensed_base_model():
    """`--base` is a separate flag and would otherwise be an unguarded way in."""
    with pytest.raises(updater.LicensedModelError):
        updater.plan(base=LICENSED_REPO)


def test_the_refusal_happens_in_plan_so_dry_run_shows_it():
    """`plan()` is what `--dry-run` prints. Refusing at the download instead
    would let `--dry-run` report a plan that cannot possibly succeed."""
    with pytest.raises(updater.LicensedModelError):
        updater.plan(lora=LICENSED_REPO, engine=False)


def test_skipping_the_weights_skips_the_refusal():
    """`fetch --engine-only` downloads nothing, so there is nothing to refuse.

    A guard that fired here would block a licence holder from upgrading vLLM,
    which is not what any of this is about.
    """
    assert updater.plan(weights=False, lora=LICENSED_REPO)["downloads"] == []


def test_a_public_model_still_fetches():
    assert updater.plan(lora=PUBLIC_REPO)["lora_adapter"] == PUBLIC_REPO
