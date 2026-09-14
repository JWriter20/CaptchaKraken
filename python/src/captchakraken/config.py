import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

_PINNED_MODEL_PATH = Path(__file__).with_name("pinned_model.json")


@lru_cache(maxsize=1)
def pinned() -> Dict[str, Any]:
    with _PINNED_MODEL_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def base_url() -> str:
    return (
        os.getenv("VLLM_BASE_URL")
        or _base_url_from_credentials_file()
        or f"http://localhost:{port()}/v1"
    )


_HOSTED_HOSTS = ("api.captchakraken.com",)


def hosted_hosts() -> tuple:
    extra = os.getenv("CAPTCHA_HOSTED_HOSTS", "")
    return _HOSTED_HOSTS + tuple(
        h.strip().lower() for h in extra.split(",") if h.strip())


def is_hosted_endpoint(url: Optional[str] = None) -> bool:
    from urllib.parse import urlparse

    try:
        host = (urlparse(url or base_url()).hostname or "").lower()
    except Exception:
        return False
    return host in hosted_hosts()


def state_dir() -> Path:
    return Path(os.getenv("CAPTCHA_KRAKEN_STATE_DIR", str(Path.home() / ".captchakraken")))


def credentials_path() -> Path:
    return state_dir() / "credentials"


_KEY_NAMES = ("CAPTCHA_KRAKEN_API_KEY", "VLLM_API_KEY")
_BASE_URL_NAMES = ("CAPTCHA_KRAKEN_BASE_URL", "VLLM_BASE_URL")


def _read_credentials_file() -> Dict[str, str]:
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
            if name not in _KEY_NAMES and name not in _BASE_URL_NAMES:
                continue
            found.setdefault(name, value.strip().strip("'\""))
        elif "" not in found:
            found[""] = line.strip("'\"")

    return found


def _first_present(values: Dict[str, str], names) -> str:
    for name in names:
        value = values.get(name, "").strip()
        if value:
            return value
    return ""


def _key_from_credentials_file() -> str:
    values = _read_credentials_file()
    return _first_present(values, _KEY_NAMES) or values.get("", "").strip()


def _base_url_from_credentials_file() -> str:
    return _first_present(_read_credentials_file(), _BASE_URL_NAMES)


def api_key() -> str:
    return (
        os.getenv("CAPTCHA_KRAKEN_API_KEY")
        or os.getenv("VLLM_API_KEY")
        or _key_from_credentials_file()
        or "EMPTY"
    )


def _registry_default(field: str) -> Optional[str]:
    try:
        from . import prompts

        models = prompts.registered_models()
        entry = None
        pin = os.getenv("CAPTCHA_LORA_ADAPTER") or os.getenv("CAPTCHA_LORA_NAME")
        if pin:
            entry = models.get(prompts.canonical_model_id(pin) or "")
        if entry is None:
            entry = models.get(prompts.latest_model() or "")
        value = (entry or {}).get(field)
        return value if isinstance(value, str) else None
    except Exception:
        return None


def base_model() -> str:
    return os.getenv("CAPTCHA_BASE_MODEL") or _registry_default("base_model") \
        or pinned()["base_model"]


def lora_adapter() -> str:
    from . import prompts

    return (os.getenv("CAPTCHA_LORA_ADAPTER") or prompts.latest_model()
            or pinned()["lora_adapter"])


def lora_revision() -> str:
    return os.getenv("CAPTCHA_LORA_REVISION") or _registry_default("lora_revision") \
        or pinned()["lora_revision"]


def lora_name() -> str:
    pin = os.getenv("CAPTCHA_LORA_NAME")
    if pin:
        return pin
    if is_hosted_endpoint():
        hosted = _hosted_default_name()
        if hosted:
            return hosted
    return _registry_default("lora_name") or pinned()["lora_name"]


def _hosted_default_name() -> Optional[str]:
    try:
        from . import prompts

        repo = prompts.hosted_default_model()
        if not repo:
            return None
        aliases = [a for a, target in prompts.served_aliases().items()
                   if target == repo and not a.startswith("_")]
        for alias in aliases:
            if prompts.experts(alias):
                return alias
        if aliases:
            return aliases[0]
        entry = prompts.registered_models().get(repo) or {}
        value = entry.get("lora_name")
        return value if isinstance(value, str) else None
    except Exception:
        return None


def port() -> int:
    return int(os.getenv("VLLM_PORT", "8000"))


def gpu_memory_utilization() -> float:
    return float(os.getenv("VLLM_GPU_MEMORY_UTILIZATION", "0.80"))


def max_model_len() -> int:
    return int(os.getenv("VLLM_MAX_MODEL_LEN", "65536"))


def max_lora_rank() -> int:
    return int(os.getenv("VLLM_MAX_LORA_RANK", "64"))


def autostart_enabled() -> bool:
    return os.getenv("CAPTCHA_KRAKEN_AUTOSTART", "1") != "0"


def extra_serve_args() -> list:
    raw = os.getenv("VLLM_EXTRA_ARGS", "").strip()
    return raw.split() if raw else []
