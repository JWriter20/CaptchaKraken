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
    """OpenAI-compatible endpoint, e.g. http://localhost:8000/v1.

    Precedence mirrors `api_key()` exactly — env, then the credentials file,
    then the local default — because the two values are set together and a
    reader who has learned one ordering should not have to learn a second.

      1. VLLM_BASE_URL. An explicit override always wins; self-hosters and the
         dev environment depend on this and must not be silently redirected.
      2. The credentials file. Written by the MCP signup flow alongside the key,
         so onboarding configures the endpoint in the same step and a hosted
         user needs no env plumbing at all.
      3. localhost. Unchanged, and still correct: someone with no credentials
         file is by definition not a hosted user, so the self-hosted default is
         the right guess for them.

    The bug this closes: a camoufox user who signed up through the MCP had a
    valid key and no endpoint, so every solve dialled a local port with nothing
    behind it and failed with connection-refused — a message that says nothing
    about the fact that their requests were meant to go to api.captchakraken.com.
    """
    return (
        os.getenv("VLLM_BASE_URL")
        or _base_url_from_credentials_file()
        or f"http://localhost:{port()}/v1"
    )


def state_dir() -> Path:
    """Shared state dir for the pidfile, lockfile, server log, and credentials."""
    return Path(os.getenv("CAPTCHA_KRAKEN_STATE_DIR", str(Path.home() / ".captchakraken")))


def credentials_path() -> Path:
    return state_dir() / "credentials"


# Names the credentials file may use for each value. Two spellings each, because
# the file doubles as something a user can `source` — the VLLM_* names are the
# env vars this module already honours, and the CAPTCHA_KRAKEN_* names are what
# the product calls them. Accepting both costs nothing and spares anyone the
# discovery that they picked the wrong one.
_KEY_NAMES = ("CAPTCHA_KRAKEN_API_KEY", "VLLM_API_KEY")
_BASE_URL_NAMES = ("CAPTCHA_KRAKEN_BASE_URL", "VLLM_BASE_URL")


def _read_credentials_file() -> Dict[str, str]:
    """Parse the file the MCP server's signup flow writes.

    Hosted-API onboarding writes the key to a 0600 file rather than returning it
    through the agent transcript, so it never lands in an LLM context window.
    Reading it here is what lets a solve authenticate with NO env vars set.

    Returns a mapping of recognised name -> value. The special key ``""`` holds a
    BARE TOKEN — a file containing nothing but `ck_live_…`, which is what the
    original single-value format was and what a user hand-writing this file is
    most likely to produce. Dropping that would silently break every existing
    credentials file, so it is still accepted and still means "the API key".

    Deliberately tolerant of quoting and comments: the file is written by a
    *separate* tool, and a format mismatch here surfaces as a baffling 401
    rather than an obvious parse error. Returns {} whenever the file is missing
    or unreadable — the self-hosted case, which must stay silent.
    """
    try:
        text = credentials_path().read_text(encoding="utf-8")
    except OSError:
        return {}

    found: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            name, _, value = line.partition("=")
            name = name.strip()
            # An env-file with unrelated keys must not yield a bogus token.
            if name not in _KEY_NAMES and name not in _BASE_URL_NAMES:
                continue
            found.setdefault(name, value.strip().strip("'\""))
        elif "" not in found:
            # A bare token. Only the first such line counts; later prose in a
            # hand-edited file must not overwrite the credential.
            found[""] = line.strip("'\"")

    return found


def _first_present(values: Dict[str, str], names) -> str:
    """First non-empty value among `names`, in the order given."""
    for name in names:
        value = values.get(name, "").strip()
        if value:
            return value
    return ""


def _key_from_credentials_file() -> str:
    """The API key from the credentials file, or "" if there isn't one."""
    values = _read_credentials_file()
    return _first_present(values, _KEY_NAMES) or values.get("", "").strip()


def _base_url_from_credentials_file() -> str:
    """The endpoint from the credentials file, or "" if it names none.

    A bare-token file deliberately yields nothing here: it carries no endpoint,
    and inventing the hosted one for it would silently redirect a self-hoster
    who wrote a token into that file by hand.
    """
    return _first_present(_read_credentials_file(), _BASE_URL_NAMES)


def api_key() -> str:
    """Bearer token, in precedence order.

    Env first so an explicit override always wins, then the credentials file so
    the MCP/hosted path needs no env plumbing at all, then "EMPTY" (which leaves
    a self-hosted local vLLM open, as before).
    """
    return (
        os.getenv("CAPTCHA_KRAKEN_API_KEY")
        or os.getenv("VLLM_API_KEY")
        or _key_from_credentials_file()
        or "EMPTY"
    )


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
