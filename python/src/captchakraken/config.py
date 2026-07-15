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
  CAPTCHA_LORA_NAME       served adapter name the client asks for as `model`
  VLLM_PORT, VLLM_GPU_MEMORY_UTILIZATION, VLLM_MAX_MODEL_LEN, VLLM_MAX_LORA_RANK
  CAPTCHA_KRAKEN_AUTOSTART=0   disable auto-starting a local server on first use
"""

import os


# ── Inference endpoint ──────────────────────────────────────────────────────
def base_url() -> str:
    """OpenAI-compatible endpoint, e.g. http://localhost:8000/v1."""
    return os.getenv("VLLM_BASE_URL", f"http://localhost:{port()}/v1")


def api_key() -> str:
    return os.getenv("CAPTCHA_KRAKEN_API_KEY") or os.getenv("VLLM_API_KEY") or "EMPTY"


# ── Model identity (all overridable — the CLI itself is model-agnostic) ──────
def base_model() -> str:
    return os.getenv("CAPTCHA_BASE_MODEL", "Qwen/Qwen3.5-9B")


def lora_adapter() -> str:
    """HF repo id or local path of the captcha adapter served on top of the base."""
    return os.getenv("CAPTCHA_LORA_ADAPTER", "CaptchaKraken/CaptchaKraken_v1")


def lora_name() -> str:
    """The served LoRA name the client sends as the `model` field."""
    return os.getenv("CAPTCHA_LORA_NAME", "captcha")


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
