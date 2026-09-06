"""The routed adapter name must reach the PAYLOAD, and only there.

`prompts.route` being right is worth nothing if the planner keeps sending
`self.model`. These tests read the JSON the client would have posted.

The unrouted half is the more important one: it is the assertion that adding
this mechanism changed nothing for `captcha`, `captcha-v12`, Twilight and
Sunlight, which is every model anyone is running today.
"""
import json
import pytest

from captchakraken import planner as P
from captchakraken import prompts


ROUTED = {
    "latest": "Acme/Plain",
    "models": {
        "Acme/Plain": {"prompt_version": "2", "lora_name": "plain"},
        "Acme/Routed": {
            "prompt_version": "2", "lora_name": "routed",
            "experts": {"grid": "routed-grid", "pixel": "routed-general",
                        "video": "routed-video", "text": "routed-text"},
        },
    },
    "served_aliases": {n: "Acme/Routed" for n in
                       ("routed", "routed-grid", "routed-general",
                        "routed-video", "routed-text")},
}


class _Resp:
    ok = True

    @staticmethod
    def json():
        return {"choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


@pytest.fixture
def posted(monkeypatch):
    """Every payload the planner would have sent, in order."""
    seen = []

    def fake_post(self, url, headers=None, json=None, timeout=None):
        seen.append(json)
        return _Resp()

    monkeypatch.setattr(P.requests.Session, "post", fake_post)
    monkeypatch.setattr(P, "ensure_server", lambda *a, **k: None)
    return seen


@pytest.fixture
def routed(monkeypatch):
    prompts.clear_cache()
    monkeypatch.setattr(prompts, "_load_registry", lambda: json.loads(json.dumps(ROUTED)))
    yield
    prompts.clear_cache()


def _png(tmp_path, name="board.png"):
    from PIL import Image
    p = tmp_path / name
    Image.new("RGB", (400, 580), "white").save(p)
    return str(p)


def _planner(model, **kw):
    return P.ActionPlanner(model=model, api_key="k", base_url="http://x/v1", **kw)


# ── routed ──────────────────────────────────────────────────────────────────

def test_a_grid_round_is_sent_to_the_grid_expert(tmp_path, posted, routed):
    _planner("routed").get_grid_selection(_png(tmp_path), rows=3, cols=3)
    assert posted[-1]["model"] == "routed-grid"


def test_a_click_round_is_sent_to_the_generalist(tmp_path, posted, routed):
    _planner("routed").get_pixel_actions(_png(tmp_path))
    assert posted[-1]["model"] == "routed-general"


def test_a_text_round_is_sent_to_the_text_expert(tmp_path, posted, routed):
    _planner("routed").get_pixel_actions(_png(tmp_path), text_mode=True)
    assert posted[-1]["model"] == "routed-text"


def test_a_keyframe_round_is_sent_to_the_video_expert(tmp_path, posted, routed):
    frames = [_png(tmp_path, f"f{i}.png") for i in range(3)]
    _planner("routed").get_keyframe_actions(frames)
    assert posted[-1]["model"] == "routed-video"


def test_one_planner_routes_every_family_in_one_solve(tmp_path, posted, routed):
    """A real solve changes family mid-solve — a board that reads as a grid,
    then a click round after the grid is rejected. The name must move with it,
    which is why the family is a per-REQUEST argument and not planner state."""
    pl = _planner("routed")
    img = _png(tmp_path)
    pl.get_grid_selection(img, rows=3, cols=3)
    pl.get_pixel_actions(img)
    assert [p["model"] for p in posted] == ["routed-grid", "routed-general"]


def test_a_pin_overrides_every_family(tmp_path, posted, routed):
    pl = _planner("routed", expert="grid")
    img = _png(tmp_path)
    pl.get_pixel_actions(img)
    pl.get_keyframe_actions([img])
    assert {p["model"] for p in posted} == {"routed-grid"}


def test_the_env_pin_works_the_same(tmp_path, posted, routed, monkeypatch):
    monkeypatch.setenv(prompts.EXPERT_ENV, "video")
    _planner("routed").get_pixel_actions(_png(tmp_path))
    assert posted[-1]["model"] == "routed-video"


def test_an_explicit_expert_beats_the_env(tmp_path, posted, routed, monkeypatch):
    monkeypatch.setenv(prompts.EXPERT_ENV, "video")
    _planner("routed", expert="grid").get_pixel_actions(_png(tmp_path))
    assert posted[-1]["model"] == "routed-grid"


# ── the pin fails LOUDLY, and early ─────────────────────────────────────────

def test_a_bad_pin_fails_when_the_planner_is_built_not_mid_solve(routed):
    with pytest.raises(ValueError, match="unknown expert"):
        _planner("routed", expert="gird")


def test_pinning_an_expert_on_a_single_adapter_model_refuses(routed):
    """Not ignored. A benchmark that measured the generalist and reported it as
    the expert is a number nobody can catch."""
    with pytest.raises(ValueError, match="declares no experts"):
        _planner("plain", expert="grid")


# ── unrouted: the wire is unchanged ─────────────────────────────────────────

@pytest.mark.parametrize("model", ["captcha", "captcha-v12",
                                   "someone-elses-adapter"])
def test_an_unrouted_model_sends_one_name_for_every_family(tmp_path, posted, model):
    """The shipped registry, not a fixture — this is the compatibility claim."""
    prompts.clear_cache()
    pl = _planner(model)
    img = _png(tmp_path)
    pl.get_grid_selection(img, rows=3, cols=3)
    pl.get_pixel_actions(img)
    pl.get_pixel_actions(img, text_mode=True)
    assert {p["model"] for p in posted} == {model}


def test_an_unrouted_planner_does_not_read_the_registry_per_request(tmp_path, posted,
                                                                    monkeypatch):
    """Resolution can touch the Hub, so it happens once per planner. A
    per-request lookup would put a network call in front of every round."""
    prompts.clear_cache()
    pl = _planner("captcha")
    calls = []
    monkeypatch.setattr(prompts, "experts",
                        lambda m: calls.append(m) or {})
    img = _png(tmp_path)
    pl.get_pixel_actions(img)
    pl.get_grid_selection(img, rows=3, cols=3)
    assert calls == [], "experts() must be resolved at construction, not per request"


def test_the_shipped_registry_routes_abyss_end_to_end(tmp_path, posted):
    """No fixture: the real models.json, the real planner, the real payload.

    This is the assertion that the publish commit has one job left — put the
    weights behind these names — and that everything between a solve and the
    wire is already correct."""
    prompts.clear_cache()
    pl = _planner("abyss")
    img = _png(tmp_path)
    pl.get_grid_selection(img, rows=3, cols=3)
    pl.get_pixel_actions(img)
    pl.get_pixel_actions(img, text_mode=True)
    pl.get_keyframe_actions([img, img])
    assert [p["model"] for p in posted] == [
        "abyss-grid", "abyss-general", "abyss-text", "abyss-video"]


def test_abyss_reads_puzzles_at_its_own_band(tmp_path, posted):
    """The band is resolved off the same key as the prompts, so it must survive
    being asked for by an EXPERT name rather than the router's."""
    prompts.clear_cache()
    for name in ("abyss", "abyss-grid", "abyss-video"):
        assert _planner(name).pixel_budget.minimum == 518400
