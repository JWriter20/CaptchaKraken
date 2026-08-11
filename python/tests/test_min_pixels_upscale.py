"""Images below the training pixel floor must be upscaled before they are sent.

The adapters are trained with `MIN_PIXELS=200704` (448², exported by the
finetune repo's `scripts/train_unified.sh`), so every image smaller than that
is enlarged before the ViT ever sees it. Nothing did the same at inference:
vLLM is launched with no `--mm-processor-kwargs` and this client re-encoded the
file byte-for-byte, so small captchas reached the model at a geometry it was
never tuned on.

It is not a subtle degradation. Measured 2026-08-10 on real geetest_v3_slide
captures (277x285 = 78,945 px, well under the floor), same adapter, same
prompt, only the input size differing:

    gold        sent native     sent upscaled
    (649, 330)  (572, 298)      (648, 333)
    (718, 165)  (625, 135)      (716, 169)
    (747, 351)  (641, 293)      (744, 354)

80-105 px out versus 1-4 px, on every sample. Tier 2 scored the affected types
at 0.000-0.119 while types whose captures happen to exceed the floor
(recaptcha_grid_3x3, 232,000 px) scored 0.704 — the split follows image size,
not puzzle difficulty.

The deployed v1.1 adapter improves under the same change (mean error ~40 px
native, ~4 px upscaled), so this is unconditional rather than keyed to a model
generation.
"""

from __future__ import annotations

import base64
import io
import re

import pytest
from PIL import Image

from captchakraken import planner as P


class _Resp:
    ok = True

    @staticmethod
    def json():
        return {"choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


@pytest.fixture
def captured(monkeypatch):
    """Send one request, hand back the payload instead of doing any I/O."""
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["payload"] = json
        return _Resp()

    monkeypatch.setattr(P.requests, "post", fake_post)
    monkeypatch.setattr(P, "ensure_server", lambda *a, **k: None)
    return seen


def _sent_images(payload):
    """Every image in the request, decoded back to a PIL image."""
    out = []
    for part in payload["messages"][-1]["content"]:
        if part.get("type") != "image_url":
            continue
        b64 = re.sub(r"^data:[^;]+;base64,", "", part["image_url"]["url"])
        out.append(Image.open(io.BytesIO(base64.b64decode(b64))))
    return out


def test_a_small_image_is_upscaled_to_the_training_floor(tmp_path, captured):
    small = tmp_path / "slider.png"
    Image.new("RGB", (277, 285), "white").save(small)
    assert 277 * 285 < P.MIN_PIXELS, "fixture must start below the floor"

    P.ActionPlanner(api_key="k", base_url="http://x/v1")._chat_with_images(
        "prompt", [str(small)])

    (sent,) = _sent_images(captured["payload"])
    w, h = sent.size
    assert w * h >= P.MIN_PIXELS, (
        f"sent {w}x{h} = {w * h} px, below the {P.MIN_PIXELS} px training floor — "
        "this is the geometry mismatch that put slider coordinates 80-105 px out")
    assert abs((w / h) - (277 / 285)) < 0.01, "aspect ratio must be preserved"


def test_an_image_above_the_floor_is_left_alone(tmp_path, captured):
    """Upscaling is a floor, not a resize. A capture already large enough must
    reach the model untouched — re-encoding every screenshot would cost time and
    fidelity for the types that were never broken."""
    big = tmp_path / "grid.png"
    Image.new("RGB", (400, 580), "white").save(big)
    assert 400 * 580 > P.MIN_PIXELS

    P.ActionPlanner(api_key="k", base_url="http://x/v1")._chat_with_images(
        "prompt", [str(big)])

    (sent,) = _sent_images(captured["payload"])
    assert sent.size == (400, 580)
