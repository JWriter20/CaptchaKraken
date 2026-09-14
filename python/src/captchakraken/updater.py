import os
import shutil
import subprocess
import sys

from . import config


ENGINE_PACKAGES = ["vllm", "huggingface_hub"]


def _hf_bin() -> "str | None":
    here = os.path.dirname(sys.executable)
    for name in ("hf", "huggingface-cli"):
        sibling = os.path.join(here, name)
        if os.path.exists(sibling) and os.access(sibling, os.X_OK):
            return sibling
    return shutil.which("hf") or shutil.which("huggingface-cli")


def _download_cmd(repo_id: str) -> "list[str]":
    hf = _hf_bin()
    if hf:
        return [hf, "download", repo_id]
    return [
        sys.executable, "-c",
        "import sys; from huggingface_hub import snapshot_download; "
        "snapshot_download(sys.argv[1])",
        repo_id,
    ]


def _pip_upgrade_cmd() -> "list[str]":
    return [sys.executable, "-m", "pip", "install", "--upgrade", *ENGINE_PACKAGES]


class LicensedModelError(RuntimeError):
    pass


def _refuse_licensed(repo_id: str) -> None:
    from . import prompts

    if not prompts.is_licensed(repo_id):
        return
    raise LicensedModelError(
        f"{repo_id} is a LICENSED model: its weights are not published and "
        "there is nothing at that Hub id to download.\n"
        "  Reach it through the hosted API (https://api.captchakraken.com), "
        "which needs no weights at all,\n"
        "  or ask us about a self-hosting licence: https://captchakraken.com/contact\n"
        "  To fetch a downloadable model instead, unset CAPTCHA_LORA_ADAPTER "
        "or point it at a published one."
    )


def _needs_auth(*repo_ids: "str | None") -> "list[str]":
    from . import prompts

    return [r for r in repo_ids if r and prompts.requires_auth(r)]


def plan(
    *,
    weights: bool = True,
    engine: bool = True,
    restart: bool = True,
    base: "str | None" = None,
    lora: "str | None" = None,
) -> dict:
    base_model = base or config.base_model()
    lora_adapter = lora or config.lora_adapter()
    base_url = config.base_url()

    if weights:
        _refuse_licensed(lora_adapter)
        _refuse_licensed(base_model)

    repos = [lora_adapter, base_model] if weights else []
    return {
        "weights": weights,
        "engine": engine,
        "restart": restart,
        "base_model": base_model,
        "lora_adapter": lora_adapter,
        "hf_org": "https://huggingface.co/CaptchaKraken",
        "downloads": [_download_cmd(r) for r in repos],
        "needs_auth": _needs_auth(*repos),
        "engine_upgrade": _pip_upgrade_cmd() if engine else None,
        "server": {"base_url": base_url, "local": _is_local(base_url)},
    }


def _is_local(base_url: str) -> bool:
    from .server_manager import is_local

    return is_local(base_url)


def _log(msg: str) -> None:
    print(f"[fetch] {msg}", file=sys.stderr)


def _run(cmd: "list[str]") -> None:
    _log("$ " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def fetch(
    *,
    weights: bool = True,
    engine: bool = True,
    restart: bool = True,
    dry_run: bool = False,
    base: "str | None" = None,
    lora: "str | None" = None,
) -> dict:
    p = plan(weights=weights, engine=engine, restart=restart, base=base, lora=lora)
    if dry_run:
        return {**p, "dry_run": True}

    if weights:
        _log(f"Pulling latest weights from {p['hf_org']} (+ base) …")
        for repo in p["needs_auth"]:
            _log(f"{repo} is a PRIVATE repo — this needs a HuggingFace token "
                 "authorised on it (`hf auth login`, or HF_TOKEN).")
        for cmd in p["downloads"]:
            _run(cmd)

    if engine:
        _log("Upgrading the vLLM serving stack …")
        _run(p["engine_upgrade"])

    server_result = None
    if restart and p["server"]["local"]:
        from . import server_manager

        if server_manager._read_pid() is not None:
            _log("Restarting the local vLLM server so new weights/engine load …")
            server_manager.stop()
            server_result = server_manager.start(background=True)
        else:
            _log("No local server running — it will pick up the update on next start.")
    elif restart:
        _log("Endpoint is remote — leaving the server you manage untouched.")

    return {
        "fetched_weights": bool(weights),
        "upgraded_engine": bool(engine),
        "server": server_result,
        "base_model": p["base_model"],
        "lora_adapter": p["lora_adapter"],
    }
