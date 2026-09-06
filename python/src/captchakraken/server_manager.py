"""
Hands-off local vLLM lifecycle.

The client never has to think about the server. When the configured endpoint is
LOCAL and nothing is answering, we start `vllm serve` exactly once — guarded by a
file lock so concurrent first-callers don't double-spawn — wait for it to become
healthy, and then proceed. When VLLM_BASE_URL points at a REMOTE host, we assume
the user manages that server and do nothing (so passing your own URL is all it
takes to opt out of local management entirely).

Public API:
    ensure_server(base_url=None)  -> called automatically before the first solve
    start(background=True)        -> `captchakraken server start`
    run_foreground()              -> `captchakraken server run`  (exec vllm)
    stop()                        -> `captchakraken server stop`
    status(base_url=None)         -> dict for `captchakraken server status`
"""

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import config

# One shared state dir for the pidfile, lockfile, server log, and the hosted-API
# credentials file. Defined in `config` so the path has a single definition.
STATE_DIR = config.state_dir()
LOCK_FILE = STATE_DIR / "vllm.lock"
PID_FILE = STATE_DIR / "vllm.pid"
LOG_FILE = STATE_DIR / "vllm.log"

# vLLM cold-starts slowly (weights + LoRA + KV cache warmup). Give it room.
STARTUP_TIMEOUT_S = int(os.getenv("CAPTCHA_KRAKEN_STARTUP_TIMEOUT", "600"))


def _log(msg: str) -> None:
    if os.getenv("CAPTCHA_DEBUG", "0") == "1":
        print(f"[server] {msg}", file=sys.stderr)


def _server_root(base_url: str) -> str:
    p = urlparse(base_url)
    return f"{p.scheme}://{p.netloc}"


def is_local(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1", ""}


def is_healthy(base_url: str, timeout: float = 2.0) -> bool:
    """True once vLLM answers /health. Cheap enough for the hot-path fast check."""
    try:
        r = requests.get(_server_root(base_url) + "/health", timeout=timeout)
        return r.ok
    except requests.RequestException:
        return False


def _vllm_bin() -> "str | None":
    """Locate the `vllm` executable. Prefer the one next to the CURRENT Python
    interpreter (the venv the CLI is running in) — the JS driver invokes us via
    the venv python without the venv's bin on PATH, so `shutil.which` alone would
    miss it. Fall back to PATH."""
    sibling = os.path.join(os.path.dirname(sys.executable), "vllm")
    if os.path.exists(sibling) and os.access(sibling, os.X_OK):
        return sibling
    return shutil.which("vllm")


def _vllm_available() -> bool:
    return _vllm_bin() is not None


def _read_pid() -> "int | None":
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)  # signal 0 == liveness probe
        return pid
    except OSError:
        return None


def build_serve_command() -> "list[str]":
    """The `vllm serve …` argv, assembled entirely from config (model-agnostic).

    --enable-tower-connector-lora is REQUIRED: without it vLLM silently drops the
    vision-tower half of the LoRA and grid accuracy collapses.
    """
    return [
        _vllm_bin() or "vllm", "serve", config.base_model(),
        "--reasoning-parser", "qwen3",
        "--enable-lora", "--enable-tower-connector-lora",
        "--max-lora-rank", str(config.max_lora_rank()),
        "--max-model-len", str(config.max_model_len()),
        "--gpu-memory-utilization", str(config.gpu_memory_utilization()),
        "--trust-remote-code",
        "--port", str(config.port()),
        "--lora-modules", f"{config.lora_name()}={config.lora_adapter()}",
        *config.extra_serve_args(),
    ]


def _serve_env() -> dict:
    env = dict(os.environ)
    # The server's bearer must match what the client sends. api_key() falls back
    # to "EMPTY"; only forward a real key (leaving vLLM open when none is set).
    key = config.api_key()
    if key and key != "EMPTY":
        env["VLLM_API_KEY"] = key
    return env


def _spawn() -> subprocess.Popen:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not _vllm_available():
        raise RuntimeError(
            "vLLM is not installed, so a local server can't be started. Run the "
            "setup script (./setup.sh) or `pip install \"captchakraken[serve]\"`, "
            "or point VLLM_BASE_URL at a server you already run."
        )
    cmd = build_serve_command()
    _log("starting: " + " ".join(cmd))
    logf = open(LOG_FILE, "ab", buffering=0)
    logf.write(f"\n=== captchakraken starting vLLM at {time.strftime('%Y-%m-%dT%H:%M:%S')} ===\n"
               .encode())
    # Detach into its own process group so it survives the caller and a later
    # `server stop` can signal the whole group.
    proc = subprocess.Popen(
        cmd,
        stdout=logf,
        stderr=logf,
        stdin=subprocess.DEVNULL,
        env=_serve_env(),
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid))
    return proc


def _wait_healthy(base_url: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_healthy(base_url, timeout=2.0):
            return True
        # If the child died, stop waiting and surface the log.
        pid = _read_pid()
        if pid is None:
            return False
        time.sleep(2.0)
    return False


def ensure_server(base_url: "str | None" = None) -> None:
    """Guarantee an endpoint is reachable before the first request.

    Fast path: one /health GET when the server is already up. Otherwise, for a
    LOCAL endpoint with autostart enabled, start vLLM once (lock-guarded) and
    block until healthy. For a REMOTE endpoint we never spawn — the user owns it.
    """
    base_url = base_url or config.base_url()

    # REMOTE FIRST. There is nothing to ensure about a server we do not manage,
    # so asking it for /health before deciding that is a round trip spent to
    # reach a `return` — and up to `timeout=2.0` of it against a hosted gateway
    # that serves no /health at all. Paid once per ActionPlanner, which for a
    # caller using `solve_captcha_on_page` is once per solve.
    if not is_local(base_url):
        # Let the actual request raise a clear connection error rather than
        # silently trying to boot vLLM locally.
        return
    if is_healthy(base_url):
        return
    if not config.autostart_enabled():
        return

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # File lock so two solves racing on first-use don't both spawn a server.
    lock = _FileLock(LOCK_FILE)
    with lock:
        # Re-check under the lock: another process may have started it while we
        # queued, or a previous spawn may still be warming up.
        if is_healthy(base_url):
            return
        if _read_pid() is None:
            _spawn()
        if not _wait_healthy(base_url, STARTUP_TIMEOUT_S):
            raise RuntimeError(
                f"vLLM did not become healthy at {base_url} within "
                f"{STARTUP_TIMEOUT_S}s. See {LOG_FILE} for details."
            )


# ── CLI-facing lifecycle helpers ────────────────────────────────────────────
def start(background: bool = True) -> dict:
    base_url = config.base_url()
    if is_healthy(base_url):
        return {"status": "already-running", "base_url": base_url, "pid": _read_pid()}
    if not background:
        run_foreground()  # never returns
    _spawn()
    ok = _wait_healthy(base_url, STARTUP_TIMEOUT_S)
    return {
        "status": "running" if ok else "timeout",
        "base_url": base_url,
        "pid": _read_pid(),
        "log": str(LOG_FILE),
    }


def run_foreground() -> None:
    """Exec `vllm serve` in the foreground (replaces this process)."""
    if not _vllm_available():
        raise RuntimeError(
            "vLLM is not installed. Run ./setup.sh or "
            "`pip install \"captchakraken[serve]\"`."
        )
    cmd = build_serve_command()
    os.execvpe(cmd[0], cmd, _serve_env())


def stop() -> dict:
    pid = _read_pid()
    if pid is None:
        return {"status": "not-running"}
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    for _ in range(20):
        if _read_pid() is None:
            break
        time.sleep(0.5)
    try:
        PID_FILE.unlink()
    except OSError:
        pass
    return {"status": "stopped", "pid": pid}


def status(base_url: "str | None" = None) -> dict:
    base_url = base_url or config.base_url()
    return {
        "base_url": base_url,
        "healthy": is_healthy(base_url),
        "local": is_local(base_url),
        "pid": _read_pid(),
        "autostart": config.autostart_enabled(),
        "base_model": config.base_model(),
        "lora_adapter": config.lora_adapter(),
        "lora_name": config.lora_name(),
    }


class _FileLock:
    """Minimal POSIX advisory lock (fcntl). vLLM is Linux-only, so this suffices;
    on platforms without fcntl it degrades to a best-effort no-op."""

    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "w")
        try:
            import fcntl

            fcntl.flock(self._fh, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                import fcntl

                fcntl.flock(self._fh, fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            self._fh.close()
            self._fh = None
