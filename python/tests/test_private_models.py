"""A PRIVATE model is on the Hub, and only an authorised token opens it.

`availability: "private"` is the third value, and the temptation is to read it
as a softer `"licensed"`. It is not: the two differ in what an AUTHORISED
holder can do, which is exactly the thing the client acts on.

  licensed   nothing to download, however good your credentials. Refusing
             early is a kindness — it replaces a `RepositoryNotFoundError`
             that reads as "you are not logged in" with the truth.
  private    the repo exists and the bytes are there. Refusing early would be
             a LIE, and it would stop our own admin token from pulling weights
             it is entitled to. The 401 an unauthorised puller gets is the
             Hub's answer to give, not ours to pre-empt.

The bug this file exists to prevent is the one-line version of that confusion:
`is_licensed` written as `availability != PUBLIC`, which is what it WAS, and
which silently makes every private model unfetchable by anybody the day one is
registered.
"""
import pytest

from captchakraken import prompts, updater

PRIVATE_REPO = "CaptchaKraken/Abyss-text"
PRIVATE_SERVED = "abyss-text"
LICENSED_REPO = "CaptchaKraken/Abyss"
PUBLIC_REPO = "CaptchaKraken/CaptchaKraken-Lora-v1.2"


# ── the registry states it ──────────────────────────────────────────────────

def test_the_private_model_is_registered_private():
    assert prompts.availability(PRIVATE_REPO) == prompts.PRIVATE


def test_private_is_obtainable_and_licensed_is_not():
    """The whole distinction, in the two functions that act on it."""
    assert not prompts.is_licensed(PRIVATE_REPO)
    assert prompts.requires_auth(PRIVATE_REPO)

    assert prompts.is_licensed(LICENSED_REPO)
    assert not prompts.requires_auth(LICENSED_REPO)

    assert not prompts.is_licensed(PUBLIC_REPO)
    assert not prompts.requires_auth(PUBLIC_REPO)


def test_an_unrecognised_availability_still_fails_closed(monkeypatch):
    """Adding a third value must not turn the typo guard into a pass. A
    registry that says "privte" must not hand the weights out."""
    monkeypatch.setattr(prompts, "_REGISTRY", {
        "models": {"org/m": {"availability": "privte"}}})
    assert prompts.is_licensed("org/m")
    assert not prompts.requires_auth("org/m")


# ── fetching it is planned, not refused ─────────────────────────────────────

def test_fetch_plans_a_download_for_a_private_model():
    """The refusal that is right for `licensed` is wrong here."""
    plan = updater.plan(weights=True, engine=False, restart=False,
                        base=PUBLIC_REPO, lora=PRIVATE_REPO)
    assert any(PRIVATE_REPO in " ".join(cmd) for cmd in plan["downloads"])


def test_the_plan_says_which_repos_need_a_token():
    """--dry-run has to say "this needs a token" BEFORE the download, or the
    401 gets read as a typo — the same failure the licensed refusal prevents."""
    plan = updater.plan(weights=True, engine=False, restart=False,
                        base=PUBLIC_REPO, lora=PRIVATE_REPO)
    assert plan["needs_auth"] == [PRIVATE_REPO]


def test_a_licensed_model_is_still_refused():
    """The new value must not have opened the old door."""
    with pytest.raises(updater.LicensedModelError):
        updater.plan(weights=True, engine=False, restart=False,
                     base=PUBLIC_REPO, lora=LICENSED_REPO)


def test_a_public_only_plan_needs_no_token():
    """A guard that fires on everything is a guard that gets deleted."""
    plan = updater.plan(weights=True, engine=False, restart=False,
                        base=PUBLIC_REPO, lora=PUBLIC_REPO)
    assert plan["needs_auth"] == []


# ── the served name resolves, so prompts do not fall through to `latest` ────

def test_the_expert_name_resolves_to_its_own_entry():
    """`abyss-text` is what goes on the wire for a routed text solve. Without
    the alias, `canonical_model_id` is None and prompt resolution falls through
    to `latest` — the mispairing models.json exists to prevent."""
    assert prompts.canonical_model_id(PRIVATE_SERVED) == PRIVATE_REPO
    assert prompts.availability(PRIVATE_SERVED) == prompts.PRIVATE
