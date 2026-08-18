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
from captchakraken import prompts


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


def test_an_image_inside_an_open_band_is_left_alone(tmp_path):
    """Under an open band, upscaling is a floor and not a resize: a capture
    already large enough reaches the model byte-for-byte, because re-encoding
    every screenshot would spend time and fidelity on types that were never
    affected.

    Driven through `_encode_image` with an EXPLICIT budget rather than through
    ActionPlanner, because the planner resolves a per-model budget and this
    asserts the open-band rule itself. `test_a_flat_band_normalises_every_image`
    covers what a model that declares min == max does instead.
    """
    big = tmp_path / "grid.png"
    Image.new("RGB", (400, 580), "white").save(big)
    raw = big.read_bytes()
    open_band = prompts.PixelBudget(minimum=P.MIN_PIXELS, maximum=None, source="test")
    assert 400 * 580 > open_band.minimum

    _, b64 = P._encode_image(str(big), open_band)

    assert base64.b64decode(b64) == raw, (
        "the file was re-encoded despite already clearing the floor")


def test_a_flat_band_normalises_every_image(tmp_path):
    """A model may declare min == max, and then EVERY image is sent at exactly
    that area — including one that clears the floor.

    CaptchaKraken-Lora-v1.2 declares exactly that (a flat 720², swept
    2026-08-18), so "above the floor" stopped meaning "untouched" for it. That
    is the design, not a regression: a flat band exists so the adapter sees one
    geometry and only one. Aspect ratio is still preserved — area is clamped,
    never dimensions, because squashing moves every tile centre.
    """
    img = tmp_path / "grid.png"
    Image.new("RGB", (400, 580), "white").save(img)
    flat = prompts.PixelBudget(minimum=518_400, maximum=518_400, source="test")

    _, b64 = P._encode_image(str(img), flat)
    sent = Image.open(io.BytesIO(base64.b64decode(b64)))

    w, h = sent.size
    assert w * h >= flat.minimum, f"sent {w}x{h} = {w * h}, under the flat band"
    assert abs((w / h) - (400 / 580)) < 0.01, "aspect ratio must be preserved"


def test_the_planner_uses_the_pinned_models_band_not_the_module_default(tmp_path, captured):
    """The budget is a property of the ADAPTER, so the planner must read it from
    the registry rather than from `P.MIN_PIXELS`.

    This is the half that a module-level constant cannot express, and the reason
    the two tests above take an explicit budget: change which model is pinned and
    this legitimately changes with it.
    """
    img = tmp_path / "grid.png"
    Image.new("RGB", (400, 580), "white").save(img)
    planner = P.ActionPlanner(api_key="k", base_url="http://x/v1")

    planner._chat_with_images("prompt", [str(img)])

    (sent,) = _sent_images(captured["payload"])
    w, h = sent.size
    assert w * h >= planner.pixel_budget.minimum, (
        f"sent {w}x{h} = {w * h} px, under the pinned model's floor of "
        f"{planner.pixel_budget.minimum} ({planner.pixel_budget.source})")
    if planner.pixel_budget.maximum:
        # `(w-1)*(h-1)`, not `w*h`: the floor branch scales with ceil() on BOTH
        # dimensions, deliberately — rounding down lands just under the floor and
        # defeats the upscale. On a FLAT band (min == max, which v1.2 declares)
        # that same rounding necessarily overshoots the ceiling by up to one row
        # and one column: a 400x580 capture is sent as 598x867 = 518,466 against
        # a stated 518,400, which is 0.013% over and lands in the same ViT patch
        # grid. Asserting `<= maximum` exactly would be asserting that ceil()
        # does not round up.
        assert (w - 1) * (h - 1) <= planner.pixel_budget.maximum, (
            f"sent {w}x{h} = {w * h} px, more than a rounding step above the "
            f"pinned ceiling of {planner.pixel_budget.maximum}")
