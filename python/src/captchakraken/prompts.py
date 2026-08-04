"""Prompt resolution: every model gets the prompts it was trained on.

Why this exists
---------------
The prompt a captcha model is sent has to be the prompt it was trained on. When
they drift, nothing errors — the model answers in a schema the client does not
recognise and puzzles silently fail. That has happened: see failure-mode 3 in
the finetune repo's `scripts/check_prompt_parity.py`, where the shipped prompt
kept asking for the legacy `output`/`simulate_drag`/PascalCase schema long after
the LoRA had been retrained on `action: drag`/`drags[]`.

Hardcoding ONE generation of prompts makes that inevitable the moment a new
model trains on different ones: updating the constants breaks every already-
published model, and not updating them breaks the new one. There is no version
of "one set of constants" that is correct.

So this module ships the built-ins for EVERY generation still in service, and
`models.json` records which generation each model was trained on. A model keeps
working forever, whatever this client later ships.

Resolution order
----------------
1. ``CAPTCHA_PROMPTS_FILE`` — explicit local path. Wins over everything; use it
   for a model you built yourself and have not published.
2. ``models.json`` — the registry shipped with this client. FIRST, not last,
   because it is the only source that works for a private model (the production
   adapter is gated on the Hub and 401s) or one published before prompts.json
   existed, and because it is the source the release gate actually verifies.
3. The served model's repo on the Hub, when it looks like ``org/name`` and
   ``huggingface_hub`` is importable — covers models published after this
   client was released.
4. The built-ins for whatever generation ``latest`` is on, with a warning.

A prompt lookup must never fail a solve, so every step degrades rather than
raises.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

_MODELS_PATH = Path(__file__).with_name("models.json")
_PROMPTS_FILE_ENV = "CAPTCHA_PROMPTS_FILE"
_DISABLE_FETCH_ENV = "CAPTCHA_PROMPTS_NO_FETCH"

# The newest generation this client ships built-ins for. NOT "the version to
# send" — that is per model, resolved below. This exists so the finetune repo
# can ask "do you speak the generation I am training?" without importing us.
LATEST_PROMPT_VERSION = "2"


# ── built-in templates, one entry per generation in service ─────────────────
#
# A PURE LITERAL, deliberately: the finetune repo's check_prompt_parity.py reads
# it by AST without importing this package (which pulls in pydantic/cv2 that the
# training venv need not have). Keep it literal — an f-string, a .join(), or a
# reference to another constant makes it unreadable to that gate, and the gate
# going quiet is how prompt drift got shipped last time.
#
#   1 — CaptchaKrakenV1_Lora, Sunlight-AWQ-4bit, Twilight-FP8, CaptchaKraken_v1.1.
#       985-char action prompt. No slider clause. No animated ("video") family
#       and no distorted-text ("text") family: a v1 model cannot answer either
#       request, which is why both are null rather than a copy of v2's.
#   2 — adds the PUZZLE PIECE SLIDER clause and the animated + text families.
#       Byte-identical to src/synthetic/reasoning/instructions.py
#       ::PIXEL_INSTRUCTION_TEMPLATE, ::VIDEO_INSTRUCTION_TEMPLATE and
#       ::TEXT_INSTRUCTION in the finetune repo at PROMPT_VERSION 2.
#
# Do not edit a published generation's text. Ever. Those models are frozen and
# so are their prompts; a change here is a change to what an already-shipped
# model is asked, which is the bug this whole module exists to prevent. New
# text means a NEW generation.
BUILTIN_PROMPTS = {
    "1": {
        "action_pixel": (
            "Your task is to solve the captcha. Read the instruction at the top of the image carefully.\n\n"
            "Look at the puzzle and decide what action solves it. All coordinates you return must be on a "
            "normalized 0–1000 image scale (top-left = (0, 0), bottom-right = (1000, 1000)).\n\n"
            "Name WHAT each object is with a short 1–2 word label, then give its position. "
            "Choose ONE response:\n\n"
            "FOR CLICK PUZZLES:\n"
            "  Label each thing you click and give its point — subjects[i] names points[i]:\n"
            "  → \"action\": \"click\", \"subjects\": [\"<label>\", ...], "
            "\"points\": [[x1, y1], [x2, y2], ...]\n\n"
            "FOR DRAG PUZZLES:\n"
            "  Drag ONE item at a time. Label the source (the piece you pick up) and the destination "
            "(where it belongs), each with a short 1–2 word label, and give both points:\n"
            "  → \"action\": \"drag\", \"drags\": [{ \"source\": \"<label>\", \"from\": [x, y], "
            "\"destination\": \"<label>\", \"to\": [x, y] }, ...]\n\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            "  \"action\": \"click\", \"subjects\": [ ... ], \"points\": [ ... ]\n"
            "  // OR \"action\": \"drag\", \"drags\": [ ... ]\n"
            "}"
        ),
        "grid": (
            "Solve the captcha grid by choosing the cell numbers that match the description "
            "from the captcha image prompt.\n\nGrid: {rows}x{cols} ({total} cells)\n{grid_hint}\n\n"
            "If no tiles match the description (e.g., they have all been cleared or none were "
            "present), return an empty list for target_ids: [].\n\n"
            "Return JSON Array: [list of cell numbers (1-{total})]"
        ),
        "video": None,
        "text": None,
    },
    "2": {
        # UNPUBLISHED as of 2026-08-03: no entry in models.json names generation
        # 2, `latest` is a generation-1 model, and no training run has produced
        # a deployable one. That is the ONLY reason its text may still move —
        # the freeze protects models that exist, and none exists here. The
        # moment a model registers against "2", this text is frozen with it and
        # a change means generation 3.
        "action_pixel": (
            "Your task is to solve the captcha. Read the instruction at the top of the image carefully.\n\n"
            "Look at the puzzle and decide what action solves it. All coordinates you return must be on a "
            "normalized 0–1000 image scale (top-left = (0, 0), bottom-right = (1000, 1000)).\n\n"
            "Name WHAT each object is with a short 1–2 word label, then give its position. "
            "Choose ONE response:\n\n"
            "FOR CLICK PUZZLES:\n"
            "  Label each thing you click and give its point — subjects[i] names points[i]:\n"
            "  → \"action\": \"click\", \"subjects\": [\"<label>\", ...], "
            "\"points\": [[x1, y1], [x2, y2], ...]\n\n"
            "FOR DRAG PUZZLES:\n"
            "  Drag ONE item at a time. Label the source (the piece you pick up) and the destination "
            "(where it belongs), each with a short 1–2 word label, and give both points:\n"
            "  → \"action\": \"drag\", \"drags\": [{ \"source\": \"<label>\", \"from\": [x, y], "
            "\"destination\": \"<label>\", \"to\": [x, y] }, ...]\n\n"
            "FOR PUZZLE PIECE SLIDER PUZZLES:\n"
            "  A single jigsaw piece has to end up in the piece-shaped slot cut into the picture. "
            "Do not pick up the piece or the slider handle — leave the source EMPTY and give only "
            "the destination, the CENTER OF THE SLOT the piece belongs in:\n"
            "  → \"action\": \"drag\", \"drags\": [{ \"source\": \"\", \"from\": [], "
            "\"destination\": \"<label>\", \"to\": [x, y] }]\n\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            "  \"action\": \"click\", \"subjects\": [ ... ], \"points\": [ ... ]\n"
            "  // OR \"action\": \"drag\", \"drags\": [ ... ]\n"
            "}"
        ),
        "grid": (
            "Solve the captcha grid by choosing the cell numbers that match the description "
            "from the captcha image prompt.\n\nGrid: {rows}x{cols} ({total} cells)\n{grid_hint}\n\n"
            "A cell that is already selected — small checkmark badge, border or highlight — "
            "still counts. Include it if it matches.\n\n"
            "A cell being REPLACED does not: a large checkmark over the middle of the picture, "
            "a picture fading to white, or a new picture fading in. That cell is on its way to "
            "showing something else, so leave it out however well it matches.\n\n"
            "If no cells match the description, return an empty list for target_ids: [].\n\n"
            "Return JSON Array: [list of cell numbers (1-{total})]"
        ),
        "video": (
            "Your task is to solve the captcha. This challenge is animated, so instead of one "
            "picture you are given {n} still keyframes cut from a short recording of it, in "
            "order: {listing}.\n\n"
            "Every keyframe shows the SAME puzzle at a different moment. Read the instruction at "
            "the top of the keyframes carefully. What you need to act on may be visible in only "
            "some of the frames — sprites fade in and out, boards cycle their contents — so pick "
            "the ONE frame in which your target is clearest and report its number as \"frame\".\n\n"
            "The frame number is there so the solver knows WHEN to press the mouse: it waits for the "
            "widget to look like that frame before clicking. If your answer does not depend on the "
            "frame — the target is in the same place in every one of them — then there is nothing to "
            "wait for, and you may leave \"frame\" out entirely.\n\n"
            "Read your coordinates off THAT frame. All coordinates must be on a normalized 0–1000 "
            "image scale (top-left = (0, 0), bottom-right = (1000, 1000)).\n\n"
            "Name WHAT each object is with a short 1–2 word label, then give its position. "
            "Choose ONE response:\n\n"
            "FOR CLICK PUZZLES:\n"
            "  Label each thing you click and give its point — subjects[i] names points[i]:\n"
            "  → \"frame\": <1-{n}>, \"action\": \"click\", \"subjects\": [\"<label>\", ...], "
            "\"points\": [[x1, y1], [x2, y2], ...]\n\n"
            "FOR DRAG PUZZLES:\n"
            "  Drag ONE item at a time. Label the source (the piece you pick up) and the "
            "destination (where it belongs), each with a short 1–2 word label, and give both "
            "points:\n"
            "  → \"frame\": <1-{n}>, \"action\": \"drag\", \"drags\": [{{ \"source\": \"<label>\", "
            "\"from\": [x, y], \"destination\": \"<label>\", \"to\": [x, y] }}, ...]\n\n"
            "Respond ONLY with JSON:\n"
            "{{\n"
            "  \"frame\": <1-{n}>,   // omit if your answer holds in every frame\n"
            "  \"action\": \"click\", \"subjects\": [ ... ], \"points\": [ ... ]\n"
            "  // OR \"action\": \"drag\", \"drags\": [ ... ]\n"
            "}}"
        ),
        "text": (
            "Your task is to solve the captcha. The image shows a short code drawn in distorted, "
            "overlapping or warped characters, sometimes over a busy background.\n\n"
            "Read the code exactly as printed. Preserve letter case when the characters clearly "
            "show it, and do not add spaces the image does not show. Decoration — strike-through "
            "lines, dots, blobs, background texture — is not part of the code.\n\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            "  \"action\": \"type\", \"text\": \"<the code>\"\n"
            "}"
        ),
    },
}


# ── the registry ────────────────────────────────────────────────────────────

def _load_registry() -> Dict[str, Any]:
    try:
        with _MODELS_PATH.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):  # a broken registry must not fail a solve
        return {}


_REGISTRY: Optional[Dict[str, Any]] = None


def registry() -> Dict[str, Any]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_registry()
    return _REGISTRY


def registered_models() -> Dict[str, Dict[str, Any]]:
    """Every model this client knows about, repo id -> entry."""
    return {k: v for k, v in (registry().get("models") or {}).items()
            if isinstance(v, dict)}


def latest_model() -> Optional[str]:
    """The model you get when you do not pin one."""
    return registry().get("latest")


def canonical_model_id(model: Optional[str]) -> Optional[str]:
    """Repo id for `model`, resolving a served alias.

    A hosted endpoint serves a BARE NAME (`captcha`), not a repo id, so the
    string the client sends as `model` is often not something the registry can
    be keyed by. Without this indirection every hosted solve would fall through
    to the default-version branch and lose the whole point of the map.
    """
    if not model:
        return None
    if model in registered_models():
        return model
    alias = (registry().get("served_aliases") or {}).get(model)
    return alias if isinstance(alias, str) else None


# ── prompt sets ─────────────────────────────────────────────────────────────

@dataclass
class PromptSet:
    """The prompts to send one particular model, and where they came from."""

    version: str
    action_prompt: str
    grid_template: str
    video_template: Optional[str]
    text_template: Optional[str]
    grid_by_type: Dict[str, str]
    source: str

    def grid_prompt(self, *, rows: int, cols: int, grid_hint: str = "",
                    puzzle_type: Optional[str] = None) -> str:
        """A published prompts.json carries grid prompts already rendered per
        puzzle type; prefer that exact string when the type is known, since it
        is literally what the model trained on. Otherwise render the template,
        which is how this client has always built them."""
        if puzzle_type and puzzle_type in self.grid_by_type:
            return self.grid_by_type[puzzle_type]
        return self.grid_template.format(rows=rows, cols=cols,
                                         total=rows * cols, grid_hint=grid_hint)

    def video_prompt(self, n_keyframes: int) -> str:
        """The prompt for a challenge served as `n_keyframes` stills.

        The count is in the text because the model has no other way to know how
        many images arrived — frame identities live in the prompt, not in the
        image payload.
        """
        n = int(n_keyframes)
        if n < 1:
            raise ValueError(f"a keyframe request needs at least one frame, got {n_keyframes}")
        if self.video_template is None:
            raise ValueError(
                f"prompt generation {self.version} has no animated-puzzle prompt — "
                f"model was trained before the video family existed. Use a model on "
                f"generation 2 or later for animated captchas."
            )
        listing = ", ".join(f"frame {i}" for i in range(1, n + 1))
        return self.video_template.format(n=n, listing=listing)

    def text_prompt(self) -> str:
        """The prompt for a DISTORTED-TEXT captcha — one where the answer is a
        string typed into the widget's box, not a point on the picture.

        Asking for this when the generation has no such family is a hard error
        rather than a silent fall back to the click/drag prompt: that prompt
        asks for coordinates the puzzle has no use for, so a v1 model handed a
        BotDetect image answers with a point, the driver clicks a random spot on
        the letters, and the box stays empty. Failing loudly here names the
        actual problem — the model predates the family.
        """
        if self.text_template is None:
            raise ValueError(
                f"prompt generation {self.version} has no distorted-text prompt — "
                f"model was trained before the text family existed. Use a model on "
                f"generation 2 or later for BotDetect/MTCaptcha/Yandex text captchas."
            )
        return self.text_template


def builtin(version: str) -> Optional[PromptSet]:
    """The built-in PromptSet for a generation, or None if we don't ship it."""
    spec = BUILTIN_PROMPTS.get(str(version))
    if spec is None:
        return None
    return PromptSet(
        version=str(version),
        action_prompt=spec["action_pixel"],
        grid_template=spec["grid"],
        video_template=spec["video"],
        text_template=spec["text"],
        grid_by_type={},
        source=f"built-in v{version}",
    )


def _from_doc(doc: Dict[str, Any], source: str) -> PromptSet:
    """A published prompts.json. Anything it omits falls back to the built-ins
    for the version it declares — a partial file should narrow what we take from
    it, not silently drop a whole prompt family."""
    version = str(doc.get("prompt_version", LATEST_PROMPT_VERSION))
    base = builtin(version) or builtin(LATEST_PROMPT_VERSION)
    templates = doc.get("templates") or {}
    grid_by_type = {k.split("grid::", 1)[1]: v for k, v in templates.items()
                    if k.startswith("grid::") and isinstance(v, str)}
    return PromptSet(
        version=version,
        action_prompt=templates.get("action_pixel") or base.action_prompt,
        grid_template=templates.get("grid") or base.grid_template,
        video_template=templates.get("video") or base.video_template,
        text_template=templates.get("text") or base.text_template,
        grid_by_type=grid_by_type,
        source=source,
    )


_cache: Dict[str, PromptSet] = {}


def _warn(msg: str) -> None:
    print(f"[captchakraken] {msg}", file=sys.stderr)


def resolve(model: Optional[str]) -> PromptSet:
    """The PromptSet for `model`. Never raises — see the module docstring for
    the resolution order and why the registry comes before the Hub."""
    key = model or ""
    if key in _cache:
        return _cache[key]

    ps: Optional[PromptSet] = None

    explicit = os.getenv(_PROMPTS_FILE_ENV)
    if explicit:
        try:
            with open(explicit, encoding="utf-8") as fh:
                ps = _from_doc(json.load(fh), f"file:{explicit}")
        except Exception as exc:  # noqa: BLE001 — never fail a solve over this
            _warn(f"could not read {_PROMPTS_FILE_ENV}={explicit}: {exc}; "
                  "falling back to the registry")

    repo_id = canonical_model_id(model)
    if ps is None and repo_id:
        entry = registered_models().get(repo_id) or {}
        version = str(entry.get("prompt_version", "")) or None
        if version:
            ps = builtin(version)
            if ps is None:
                # Registered against a generation we ship no text for. The
                # release gate exists to make this unreachable; if it happens
                # anyway, say so loudly rather than guessing.
                _warn(f"{repo_id} is registered as prompt_version {version}, which this "
                      f"client ships no prompts for — using v{LATEST_PROMPT_VERSION}. "
                      "Upgrade captchakraken.")
            else:
                ps = PromptSet(**{**ps.__dict__, "source": f"registry:{repo_id}"})

    if ps is None and model and "/" in model and os.getenv(_DISABLE_FETCH_ENV, "0") != "1":
        try:
            from huggingface_hub import hf_hub_download  # lazy: optional dependency

            path = hf_hub_download(repo_id=model, filename="prompts.json")
            with open(path, encoding="utf-8") as fh:
                ps = _from_doc(json.load(fh), f"hub:{model}")
        except Exception:
            # No prompts.json, no hub, offline, or gated. All expected — the
            # registry above is the supported path; this is a bonus for models
            # published after this client shipped.
            ps = None

    if ps is None:
        default_version = str(
            (registered_models().get(latest_model() or "") or {})
            .get("prompt_version", LATEST_PROMPT_VERSION))
        ps = builtin(default_version) or builtin(LATEST_PROMPT_VERSION)
        if model:
            _warn(f"model {model!r} is not in models.json and published no prompts.json; "
                  f"using generation {ps.version}. If it was trained on different prompts, "
                  "its answers will be quietly worse — register it.")

    _cache[key] = ps
    return ps


def clear_cache() -> None:
    _cache.clear()
    global _REGISTRY
    _REGISTRY = None
