"""
Hermetic tests for the unified `captchakraken fetch` command (updater.py + the
CLI handler). No network, no pip, no HF — everything runs against the pure
`plan()` and a monkeypatched `subprocess.run`, so this is a safe pre-deploy
regression gate: it locks in WHICH repos get pulled, the engine-upgrade command,
the flag→behaviour mapping, and the argument validation / exit codes.
"""
import json
import sys

import pytest

from captchakraken import cli, config, updater


def test_plan_targets_hf_org_and_configured_repos():
    p = updater.plan()
    assert p["hf_org"] == "https://huggingface.co/CaptchaKraken"
    assert p["lora_adapter"] == config.lora_adapter()
    assert p["base_model"] == config.base_model()
    # The LoRA is pulled first, then the base it rides on.
    assert p["downloads"][0][-1] == p["lora_adapter"]
    assert p["downloads"][1][-1] == p["base_model"]
    # Engine upgrade uses the CURRENT interpreter's pip and includes vLLM.
    assert p["engine_upgrade"][:4] == [sys.executable, "-m", "pip", "install"]
    assert "vllm" in p["engine_upgrade"]


def test_plan_scoping_flags():
    assert updater.plan(weights=False)["downloads"] == []
    assert updater.plan(engine=False)["engine_upgrade"] is None


def test_plan_respects_overrides():
    p = updater.plan(base="acme/base", lora="acme/lora")
    assert p["base_model"] == "acme/base"
    assert p["lora_adapter"] == "acme/lora"
    assert [d[-1] for d in p["downloads"]] == ["acme/lora", "acme/base"]


def test_fetch_dry_run_is_side_effect_free(monkeypatch):
    calls = []
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: calls.append(a))
    out = updater.fetch(dry_run=True)
    assert out["dry_run"] is True
    assert calls == []  # dry-run must execute NOTHING


def test_cli_fetch_dry_run(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["captchakraken", "fetch", "--dry-run"])
    assert cli._handle_fetch() is True
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True


def test_cli_update_alias(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["captchakraken", "update", "--dry-run"])
    assert cli._handle_fetch() is True
    assert json.loads(capsys.readouterr().out)["dry_run"] is True


def test_cli_fetch_flag_mapping(monkeypatch):
    seen = {}
    monkeypatch.setattr(updater, "fetch", lambda **kw: seen.update(kw) or {})
    monkeypatch.setattr(
        sys, "argv",
        ["captchakraken", "fetch", "--engine-only", "--no-restart", "--dry-run"],
    )
    cli._handle_fetch()
    assert seen == {"weights": False, "engine": True, "restart": False, "dry_run": True}


def test_cli_fetch_rejects_unknown_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["captchakraken", "fetch", "--bogus"])
    with pytest.raises(SystemExit) as ei:
        cli._handle_fetch()
    assert ei.value.code == 2


def test_cli_fetch_rejects_conflicting_flags(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["captchakraken", "fetch", "--weights-only", "--engine-only"],
    )
    with pytest.raises(SystemExit) as ei:
        cli._handle_fetch()
    assert ei.value.code == 2


def test_handle_fetch_ignores_other_commands(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["captchakraken", "find-grid", "x.png"])
    assert cli._handle_fetch() is False
