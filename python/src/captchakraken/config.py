"""
Central, model-agnostic configuration for CaptchaKraken.

Every model-specific value lives here and is overridable via environment
variables, so swapping the underlying model never requires touching the solver,
planner, or CLI. The defaults point at the published unified captcha LoRA, but
nothing in the codebase hard-codes a model name outside this module.

The two variables MOST users ever set:
  VLLM_BASE_URL           where inference requests go (local or a remote server)
  CAPTCHA_KRAKEN_API_KEY  bearer token for that server

Advanced / self-hosting overrides (only needed to change WHICH model is served):
  CAPTCHA_BASE_MODEL      base weights vLLM loads         (HF id or local path)
  CAPTCHA_LORA_ADAPTER    LoRA applied on top             (HF id or local path)
  CAPTCHA_LORA_REVISION   adapter git revision to serve
  CAPTCHA_LORA_NAME       served adapter name the client asks for as `model`

Defaults for the model-identity values come from `pinned_model.json` (see
`pinned()`), which records the model + prompt pair this release was validated
against.
  VLLM_PORT, VLLM_GPU_MEMORY_UTILIZATION, VLLM_MAX_MODEL_LEN, VLLM_MAX_LORA_RANK
  CAPTCHA_KRAKEN_AUTOSTART=0   disable auto-starting a local server on first use
"""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

_PINNED_MODEL_PATH = Path(__file__).with_name("pinned_model.json")


@lru_cache(maxsize=1)
def pinned() -> Dict[str, Any]:
    """The pinned model manifest shipped with this release.

    Defines the (base model, adapter, serving prompts) triple this version was
    validated against. Env vars still override every value below — the manifest
    supplies the DEFAULT and gives CI something to assert against, so an adapter
    or prompt change is a reviewable diff instead of a silent drift.
    """
    with _PINNED_MODEL_PATH.open(encoding="utf-8") as f:
        return json.load(f)


# ── Inference endpoint ──────────────────────────────────────────────────────
def base_url() -> str:
    """OpenAI-compatible endpoint, e.g. http://localhost:8000/v1."""
    return os.getenv("VLLM_BASE_URL", f"http://localhost:{port()}/v1")


def api_key() -> str:
    return os.getenv("CAPTCHA_KRAKEN_API_KEY") or os.getenv("VLLM_API_KEY") or "EMPTY"


# ── Model identity (all overridable — the CLI itself is model-agnostic) ──────
def base_model() -> str:
    return os.getenv("CAPTCHA_BASE_MODEL", pinned()["base_model"])


def lora_adapter() -> str:
    """HF repo id or local path of the captcha adapter served on top of the base."""
    return os.getenv("CAPTCHA_LORA_ADAPTER", pinned()["lora_adapter"])


def lora_revision() -> str:
    """Git revision of the adapter repo to serve — pinning this is what makes
    'the adapter this release was tested against' a reproducible statement."""
    return os.getenv("CAPTCHA_LORA_REVISION", pinned()["lora_revision"])


def lora_name() -> str:
    """The served LoRA name the client sends as the `model` field."""
    return os.getenv("CAPTCHA_LORA_NAME", pinned()["lora_name"])


# ── Local vLLM server knobs ─────────────────────────────────────────────────
def port() -> int:
    return int(os.getenv("VLLM_PORT", "8000"))


def gpu_memory_utilization() -> float:
    return float(os.getenv("VLLM_GPU_MEMORY_UTILIZATION", "0.80"))


def max_model_len() -> int:
    return int(os.getenv("VLLM_MAX_MODEL_LEN", "65536"))


def max_lora_rank() -> int:
    return int(os.getenv("VLLM_MAX_LORA_RANK", "64"))


def autostart_enabled() -> bool:
    """Whether to auto-launch a local server on first use (default on)."""
    return os.getenv("CAPTCHA_KRAKEN_AUTOSTART", "1") != "0"


def extra_serve_args() -> list:
    """Extra flags appended verbatim to `vllm serve` (space-separated).

    Escape hatch for serving tweaks the config doesn't model directly — e.g.
    `--enforce-eager` or a smaller `--max-num-seqs` to squeeze onto a tight GPU.
    """
    raw = os.getenv("VLLM_EXTRA_ARGS", "").strip()
    return raw.split() if raw else []
