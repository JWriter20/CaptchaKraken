"""The pinned (model, prompt) pair must stay internally consistent.

`pinned_model.json` records the adapter this release was validated against and
the SHA-256 of each prompt we send it. The adapter and the prompts are a matched
pair: the LoRA is trained to answer one exact prompt, so changing either half
alone silently degrades or breaks solving.

That is not hypothetical. On 2026-07-18 the adapter was retrained on the content
schema (`action: drag` / `drags[]` / lowercase) while planner.py still asked for
the legacy `output` / `simulate_drag` / PascalCase schema. The model replied in a
hybrid of the two, the parser dropped it, and every drag puzzle failed as
"unsupported" — with nothing in CI going red, because train/grade parity inside
the finetune repo was intact. Only the SHIPPED prompt had drifted.

So: editing a serving prompt fails this test until someone updates the manifest,
which forces the question "does the pinned adapter still expect this prompt?".
Hermetic — reads constants and a JSON file, no model or network.

The prompts hashed here are THE ONES THE PINNED ADAPTER ACTUALLY GETS, resolved
through `models.json`, not the module-level `planner.PIXEL_ACTION_PROMPT`. Since
prompts became per-model those constants are aliases for the NEWEST generation,
which is not necessarily the pinned model's — hashing them made this test fail
the moment the client learned a new generation, while saying nothing about
whether the pinned pair still matched. Resolving first restores the meaning:
"the prompts this release sends the adapter it pins".
"""
import hashlib

from captchakraken import config, prompts

_PINNED = prompts.resolve(config.pinned()["lora_adapter"])

# The prompts CI guards, by the name used in pinned_model.json.
SERVING_PROMPTS = {
    "PIXEL_ACTION_PROMPT": _PINNED.action_prompt,
    "SELECT_GRID_PROMPT": _PINNED.grid_template,
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_manifest_has_every_field_config_reads():
    manifest = config.pinned()
    for key in ("base_model", "lora_adapter", "lora_revision", "lora_name", "prompt_sha256"):
        assert key in manifest, f"pinned_model.json is missing '{key}'"


def test_config_defaults_come_from_the_manifest(monkeypatch):
    """With no env overrides, config must report exactly what is pinned."""
    for var in (
        "CAPTCHA_BASE_MODEL",
        "CAPTCHA_LORA_ADAPTER",
        "CAPTCHA_LORA_REVISION",
        "CAPTCHA_LORA_NAME",
    ):
        monkeypatch.delenv(var, raising=False)

    manifest = config.pinned()
    assert config.base_model() == manifest["base_model"]
    assert config.lora_adapter() == manifest["lora_adapter"]
    assert config.lora_revision() == manifest["lora_revision"]
    assert config.lora_name() == manifest["lora_name"]


def test_env_still_overrides_the_pin(monkeypatch):
    """The pin is a default, not a lock — self-hosters must keep their override."""
    monkeypatch.setenv("CAPTCHA_LORA_ADAPTER", "someone-else/their-adapter")
    assert config.lora_adapter() == "someone-else/their-adapter"


def test_every_guarded_prompt_is_listed_in_the_manifest():
    pinned_names = set(config.pinned()["prompt_sha256"])
    assert pinned_names == set(SERVING_PROMPTS), (
        "pinned_model.json prompt_sha256 keys drifted from the prompts this test "
        "guards. A new serving prompt must be pinned too — an unpinned prompt is "
        "exactly the hole that let the 2026-07-18 drag regression ship."
    )


def test_serving_prompts_match_their_pinned_hashes():
    expected = config.pinned()["prompt_sha256"]
    drifted = {
        name: (expected[name], _sha256(text))
        for name, text in SERVING_PROMPTS.items()
        if _sha256(text) != expected[name]
    }
    assert not drifted, (
        "A serving prompt changed but pinned_model.json was not updated:\n"
        + "\n".join(f"  {n}: pinned {p[:12]}… now {a[:12]}…" for n, (p, a) in drifted.items())
        + "\n\nThe LoRA is trained to answer one exact prompt. If this change is "
        "intentional, confirm the pinned adapter still expects the new wording, "
        "run the Tier 2 static-image gate, then update prompt_sha256."
    )
