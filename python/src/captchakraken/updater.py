"""
Unified fetch/update for self-hosters.

One command pulls the latest published CaptchaKraken model from the HuggingFace
org (https://huggingface.co/CaptchaKraken) AND refreshes the local vLLM serving
stack, so upgrading picks up new model revisions + engine fixes without
re-running the whole installer:

    captchakraken fetch

What it does (all model-agnostic — repo ids come from `config`, so overriding
CAPTCHA_LORA_ADAPTER / CAPTCHA_BASE_MODEL retargets it):

  1. Re-downloads the configured captcha LoRA adapter + base model from HF. A
     no-op when you're already on the latest revision; pulls the new commit
     otherwise. (Downloads are cached under HF_HOME, same as the installer.)
  2. Upgrades the vLLM serving stack in the current environment
     (``pip install -U vllm huggingface_hub``).
  3. Restarts a locally-managed vLLM server if one is running, so the freshly
     pulled weights + engine actually take effect. Remote endpoints are left
     alone (you manage those).

Flags handled by the CLI wrapper:
  --weights-only   just re-pull weights (skip the pip upgrade)
  --engine-only    just upgrade vLLM (skip the weight download)
  --no-restart     don't bounce a running local server
  --dry-run        print the plan as JSON and do nothing (no network, no pip)
"""

import os
import shutil
import subprocess
import sys

from . import config


# The vLLM serving stack `fetch` keeps current. Mirrors the `serve` extra in
# pyproject; upgraded in-place so a self-hoster gets engine fixes without a full
# reinstall. Kept deliberately small — torch/transformers ride along with vllm.
ENGINE_PACKAGES = ["vllm", "huggingface_hub"]


def _hf_bin() -> "str | None":
    """Locate the HuggingFace CLI. Prefer the one next to the CURRENT interpreter
    (the venv the CLI runs in) — the JS driver invokes us via the venv python
    without its bin dir on PATH — then fall back to PATH. Accepts either the new
    `hf` entrypoint or the legacy `huggingface-cli`."""
    here = os.path.dirname(sys.executable)
    for name in ("hf", "huggingface-cli"):
        sibling = os.path.join(here, name)
        if os.path.exists(sibling) and os.access(sibling, os.X_OK):
            return sibling
    return shutil.which("hf") or shutil.which("huggingface-cli")


def _download_cmd(repo_id: str) -> "list[str]":
    """The argv that pulls (or refreshes) one HF repo into the local cache. Uses
    the `hf`/`huggingface-cli` binary when present; otherwise drives
    `huggingface_hub.snapshot_download` through the current interpreter so a
    fetch still works from a bare `pip install captchakraken[serve]` (no console
    script on PATH)."""
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
    """Raised when `fetch` is pointed at weights that are not downloadable."""


def _refuse_licensed(repo_id: str) -> None:
    """A licensed model has no Hub repo. Say so, instead of 404ing at the Hub.

    Without this the failure is `RepositoryNotFoundError` from huggingface_hub,
    which reads as "you are not logged in" or "typo" — so the next thing a
    self-hoster does is hunt for a token that will never exist. It is also the
    one place a licensed model's name can plausibly be typed by accident:
    CAPTCHA_LORA_ADAPTER takes any string and `fetch` hands it straight to the
    Hub.
    """
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


def plan(
    *,
    weights: bool = True,
    engine: bool = True,
    restart: bool = True,
    base: "str | None" = None,
    lora: "str | None" = None,
) -> dict:
    """Assemble the fetch plan WITHOUT running anything. Pure + side-effect-free
    so `--dry-run` and the tests can assert exactly what a real run would do.

    Raises `LicensedModelError` rather than planning a download that cannot
    succeed. Refusing HERE and not at the download call is deliberate: `plan()`
    is what `--dry-run` prints, so the refusal is visible before anyone runs the
    real thing, and there is exactly one place to keep it correct.
    """
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
        "engine_upgrade": _pip_upgrade_cmd() if engine else None,
        "server": {"base_url": base_url, "local": _is_local(base_url)},
    }


def _is_local(base_url: str) -> bool:
    # Imported lazily so a plan/dry-run doesn't drag in requests via server_manager.
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
    """Pull the latest weights + engine and (optionally) restart a local server.

    Returns a JSON-serializable summary of what was done. On --dry-run, returns
    the plan with ``"dry_run": true`` and performs no I/O.
    """
    p = plan(weights=weights, engine=engine, restart=restart, base=base, lora=lora)
    if dry_run:
        return {**p, "dry_run": True}

    if weights:
        _log(f"Pulling latest weights from {p['hf_org']} (+ base) …")
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
