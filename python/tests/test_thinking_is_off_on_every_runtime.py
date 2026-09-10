"""Thinking must be off on EVERY runtime, and they disagree about how to say so.

These weights are trained to answer straight into a fixed JSON schema, with the
empty-think prefix already in place. Let the model think instead and the answer
goes to a `reasoning` field, `content` comes back EMPTY, and the token budget is
gone before the JSON starts. Nothing errors — it reads exactly like a model that
cannot solve anything.

`chat_template_kwargs` is what vLLM and llama.cpp read. **Ollama has no such
field**; its OpenAI-compatible endpoint reads `reasoning_effort`, ignores the
kwargs object entirely, and defaults thinking ON for any model whose template
mentions `<think>`. So one field alone is silently wrong on one of the three
runtimes this client supports, and which one depends on where the user pointed
`VLLM_BASE_URL`.

Sending both is safe, not merely tolerated: vLLM derives `enable_thinking` from
`reasoning_effort` only when the caller has not set it explicitly, so the kwargs
still win there and nothing about an existing vLLM deployment changes.
"""
import json
import pytest

from captchakraken import planner as P
from captchakraken import prompts


REGISTRY = {
    "latest": "Acme/Plain",
    "models": {"Acme/Plain": {"prompt_version": "2", "lora_name": "plain"}},
}


class _Resp:
    ok = True

    @staticmethod
    def json():
        return {"choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


@pytest.fixture
def posted(monkeypatch):
    seen = []

    def fake_post(self, url, headers=None, json=None, timeout=None):
        seen.append(json)
        return _Resp()

    monkeypatch.setattr(P.requests.Session, "post", fake_post)
    monkeypatch.setattr(P, "ensure_server", lambda *a, **k: None)
    prompts.clear_cache()
    monkeypatch.setattr(prompts, "_load_registry",
                        lambda: json.loads(json.dumps(REGISTRY)))
    yield seen
    prompts.clear_cache()


def _png(tmp_path, name="board.png"):
    from PIL import Image
    p = tmp_path / name
    Image.new("RGB", (400, 580), "white").save(p)
    return str(p)


def _planner():
    return P.ActionPlanner(model="plain", api_key="k", base_url="http://x/v1")


def test_a_grid_round_turns_thinking_off_both_ways(tmp_path, posted):
    _planner().get_grid_selection(_png(tmp_path), rows=3, cols=3)

    assert posted[-1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert posted[-1]["reasoning_effort"] == "none"


def test_a_click_round_turns_thinking_off_both_ways(tmp_path, posted):
    _planner().get_pixel_actions(_png(tmp_path))

    assert posted[-1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert posted[-1]["reasoning_effort"] == "none"


def test_a_keyframe_round_turns_thinking_off_both_ways(tmp_path, posted):
    """The family that suffers most: several stills already crowd the budget."""
    frames = [_png(tmp_path, f"f{i}.png") for i in range(3)]

    _planner().get_keyframe_actions(frames)

    assert posted[-1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert posted[-1]["reasoning_effort"] == "none"


def test_every_round_of_a_solve_says_it(tmp_path, posted):
    """Not just the first request — a real solve changes family mid-solve."""
    planner = _planner()
    planner.get_grid_selection(_png(tmp_path), rows=3, cols=3)
    planner.get_pixel_actions(_png(tmp_path))

    assert len(posted) == 2
    for payload in posted:
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        assert payload["reasoning_effort"] == "none"
