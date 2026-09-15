"""Both `chat_template_kwargs.enable_thinking` and `reasoning_effort` go out: Ollama ignores the kwargs and defaults thinking on, and vLLM reads reasoning_effort only when the kwargs are unset."""

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
    frames = [_png(tmp_path, f"f{i}.png") for i in range(3)]

    _planner().get_keyframe_actions(frames)

    assert posted[-1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert posted[-1]["reasoning_effort"] == "none"


def test_every_round_of_a_solve_says_it(tmp_path, posted):
    planner = _planner()
    planner.get_grid_selection(_png(tmp_path), rows=3, cols=3)
    planner.get_pixel_actions(_png(tmp_path))

    assert len(posted) == 2
    for payload in posted:
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        assert payload["reasoning_effort"] == "none"
