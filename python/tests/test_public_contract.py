"""The published surface, pinned. Breaking it has to be a decision.

There are real paying customers on this client. Every name below is something
someone's code says out loud — an import, a config key, a CLI flag, an
environment variable, an error code — and renaming one is a silent break: their
build still succeeds and the solve stops happening. Nothing in this repo
previously noticed. The suite has thirty-odd behaviour tests and not one of them
imports `captchakraken` at the package level or asserts the export list, so
deleting a name from `__all__`, moving `solve_captcha`'s keyword-only boundary,
or renaming `CAPTCHA_PROMPTS_FILE` was green.

WHAT THIS IS NOT. It is not a second opinion on behaviour — the rest of the
suite does that. It is a snapshot of NAMES and SHAPES, and its only job is to
turn "someone renamed a thing" from a review note into a red build.

WHEN IT FAILS, there are exactly two honest answers:

  * you did not mean to → put the name back. Adding an alias beside the new
    name is almost always the right move; removing one is a MAJOR version.
  * you did mean to → run `python tests/test_public_contract.py --write` in the
    SAME commit, and say in the message what breaks and what callers should do
    instead. Bump the major version in pyproject.toml, js/package.json and
    mcp/package.json as the change requires; the diff of ../../contract.json is
    the deprecation note, and it is reviewable precisely because it is small.

The JS half of the same file is checked by js/src/contract.test.ts and the MCP
half by .github/scripts/mcp-smoke.mjs against a live handshake — one snapshot,
three ports, so the two client libraries cannot drift from each other either
(project rule 1c: Python and JS must behave the same).
"""
from __future__ import annotations

import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]          # CaptchaKraken/
CONTRACT = REPO / "contract.json"
PKG = REPO / "python" / "src" / "captchakraken"

sys.path.insert(0, str(REPO / "python" / "src"))

import captchakraken  # noqa: E402
from captchakraken import config, errors, humanize, prompts  # noqa: E402
from captchakraken.page_solver import PageSolverConfig  # noqa: E402

# WHICH CHECKOUT ANSWERED. An editable install registers a meta-path finder,
# and a meta-path finder beats `sys.path.insert(0, ...)`. So in a git worktree —
# which is how every agent on this project works, with one shared venv pointed at
# the main checkout — `import captchakraken` silently resolves to a DIFFERENT
# tree than the one under test, and this file would pin a surface nobody is
# about to publish. Caught the first time it was run from a worktree.
assert Path(captchakraken.__file__).resolve().is_relative_to(REPO), (
    f"imported captchakraken from {captchakraken.__file__}, which is outside "
    f"{REPO}. This gate would be measuring a different checkout than the one "
    f"being tested. Run it with that tree on the path first — "
    f"`pip install -e python` inside this checkout, or "
    f"PYTHONPATH={REPO / 'python' / 'src'}.")

#: Every environment variable the client reads, anywhere in the tree. Found the
#: same way a reader would — by grepping — so a new one cannot be added without
#: this file noticing and the snapshot recording it.
#:
#: TWO patterns, and the second is not optional. Half the interesting variables
#: are read through a module constant (`_PROMPTS_FILE_ENV = "CAPTCHA_PROMPTS_FILE"`,
#: then `os.environ.get(_PROMPTS_FILE_ENV)`), so an inline-literal grep misses
#: exactly the ones that matter most: CAPTCHA_PROMPTS_FILE, CAPTCHA_MIN_PIXELS,
#: CAPTCHA_MAX_PIXELS and every routing header. A first pass at this file found
#: 26 variables and silently omitted eight.
_ENV_RE = re.compile(
    r"""(?:os\.environ(?:\.get)?\(\s*|os\.getenv\(\s*|process\.env\.|process\.env\[\s*)"""
    r"""["']?([A-Z][A-Z0-9_]{2,})["']?""")
_ENV_CONST_RE = re.compile(
    r"""^\s*(?:export\s+)?(?:const\s+)?_?[A-Z][A-Za-z0-9_]*\s*(?::[^=]+)?=\s*"""
    r"""["']((?:CAPTCHA|VLLM|MIN_PIXELS|MAX_PIXELS)[A-Z0-9_]*)["']""", re.M)

#: Read at runtime by the shipped client only. Test harnesses and example
#: scripts read others (HEADLESS, CAMOUFOX_BINARY, …) and those are not a
#: promise to anyone.
_ENV_ROOTS = ("python/src", "js/src", "js/scripts", "mcp/src")


def _sig(fn) -> str:
    """A signature as text, so a moved `*` or a renamed parameter is a diff."""
    return str(inspect.signature(fn))


def _env_vars() -> dict:
    found: dict[str, list[str]] = {}
    for root in _ENV_ROOTS:
        for path in sorted((REPO / root).rglob("*")):
            if path.suffix not in (".py", ".ts", ".js", ".mjs") or ".test." in path.name:
                continue
            src = path.read_text(errors="ignore")
            rel = path.relative_to(REPO).as_posix()
            for name in _ENV_RE.findall(src) + _ENV_CONST_RE.findall(src):
                if rel not in found.setdefault(name, []):
                    found[name].append(rel)
    return {k: sorted(v) for k, v in sorted(found.items())}


def _cli_subcommands() -> list:
    """The words `main()` dispatches on, read out of cli.py.

    Dispatch is argv sniffing rather than argparse subparsers, so there is no
    parser object to interrogate — the list lives in the `sys.argv[1] in {...}`
    guards, and that is what this reads.
    """
    src = (PKG / "cli.py").read_text()
    words = set()
    # `sys.argv[1] != "serve"`, `sys.argv[1] not in {"fetch", "update"}`
    for m in re.finditer(r"argv\[1\]\s*(?:!=|==)\s*[\"']([a-z][a-z0-9-]*)[\"']", src):
        words.add(m.group(1))
    for m in re.finditer(r"argv\[1\]\s+(?:not\s+)?in\s*[{(\[]([^})\]]*)[})\]]", src):
        words |= set(re.findall(r"[\"']([a-z][a-z0-9-]*)[\"']", m.group(1)))
    # `cmd = sys.argv[1]` then `if cmd == "x"` / `cmd not in {...}`
    for m in re.finditer(r"\bcmd\s*(?:!=|==)\s*[\"']([a-z][a-z0-9-]*)[\"']", src):
        words.add(m.group(1))
    for m in re.finditer(r"\bcmd\s+(?:not\s+)?in\s*[{(\[]([^})\]]*)[})\]]", src):
        words |= set(re.findall(r"[\"']([a-z][a-z0-9-]*)[\"']", m.group(1)))
    return sorted(words)


def _error_codes() -> list:
    """The codes `errors._sentence` branches on.

    These are the GATEWAY's strings arriving over the wire, and a caller
    matching on them (retry on rate_limited, top up on insufficient_credits)
    breaks when either side renames one. `code in ("a", "b")` has to be read as
    two codes, not one — `invalid_api_key` only ever appears as the second half
    of such a tuple, and a first pass at this dropped it.
    """
    src = (PKG / "errors.py").read_text()
    out = set(re.findall(r'code\s*==\s*["\']([a-z_]+)["\']', src))
    for group in re.findall(r'code\s+in\s*\(([^)]*)\)', src):
        out |= set(re.findall(r'["\']([a-z_]+)["\']', group))
    return sorted(out)


def _solve_parser_flags() -> list:
    """The default (no-subcommand) parser: the JS driver's entire interface."""
    src = (PKG / "cli.py").read_text()
    tail = src[src.index('parser = argparse.ArgumentParser(description="CaptchaKraken v2'):]
    return sorted(set(re.findall(r'add_argument\(\s*\n?\s*"(--[a-z-]+|[a-z_]+)"', tail)))


def surface() -> dict:
    return {
        "python": {
            "version": captchakraken.__version__,
            "exports": sorted(captchakraken.__all__),
            "signatures": {
                "solve_captcha": _sig(captchakraken.solve_captcha),
                "CaptchaSolver.__init__": _sig(captchakraken.CaptchaSolver.__init__),
                "CaptchaSolver.solve": _sig(captchakraken.CaptchaSolver.solve),
                "CaptchaSolver.solve_keyframes": _sig(captchakraken.CaptchaSolver.solve_keyframes),
                "PageSolver.__init__": _sig(captchakraken.PageSolver.__init__),
                "PageSolver.solve": _sig(captchakraken.PageSolver.solve),
                "PageSolver.watch": _sig(captchakraken.PageSolver.watch),
                "solve_captcha_on_page": _sig(captchakraken.solve_captcha_on_page),
                "CaptchaKrakenAPIError.__init__": _sig(errors.CaptchaKrakenAPIError.__init__),
                "add_overlays_to_image": _sig(captchakraken.add_overlays_to_image),
                "humanize.resolve": _sig(humanize.resolve),
            },
            "page_solver_config_fields": sorted(PageSolverConfig.__dataclass_fields__),
            "solve_result_fields": sorted(
                captchakraken.SolveResult.__dataclass_fields__),
            "config_functions": sorted(
                n for n, v in vars(config).items()
                if callable(v) and not n.startswith("_")
                and getattr(v, "__module__", "") == "captchakraken.config"),
            # The codes `_sentence` branches on. These are the gateway's, and
            # a caller matching on them (retry on rate_limited, top up on
            # insufficient_credits) breaks when one is renamed on either side.
            "error_codes": _error_codes(),
            "humanization_modes": sorted(humanize.MODES),
        },
        "cli": {
            "entry_point": "captchakraken = captchakraken.cli:main",
            "subcommands": _cli_subcommands(),
            "solve_parser": _solve_parser_flags(),
            # Not introspectable — cli.py returns them as literals. Declared
            # here because the JS driver branches on them: 2 means "this puzzle
            # is unsupported, do not retry", 3 means "the API said no".
            "exit_codes": {"ok": 0, "failure": 1, "unsupported": 2, "api_error": 3},
        },
        "env": _env_vars(),
        "model_resolution": {
            "latest": prompts.latest_model(),
            "registered_models": sorted(prompts.registered_models()),
            "latest_prompt_version": prompts.LATEST_PROMPT_VERSION,
            "pinned_fallback_fields": sorted(config.pinned().keys() - {"_comment"}),
        },
    }


# ── the gate ────────────────────────────────────────────────────────────────

STORED = json.loads(CONTRACT.read_text())
LIVE = surface()

_FIX = ("\n\nIf this change is intended, run\n"
        "    python python/tests/test_public_contract.py --write\n"
        "in the same commit, bump the version the change deserves, and say in "
        "the commit message what callers have to do instead. If it is not "
        "intended, put the name back — an alias beside the new one costs "
        "nothing and keeps every published integration working.")


@pytest.mark.parametrize("section", ["python", "cli", "env", "model_resolution"])
def test_the_published_surface_has_not_moved(section):
    assert LIVE[section] == STORED[section], (
        f"the {section!r} half of the public contract changed." + _FIX)


def test_the_version_is_one_number_everywhere():
    """`captchakraken.__version__` was 2.6.0 while the wheel published 2.6.1.

    Anyone gating on the runtime attribute — the only one importable code can
    see — read a version that had not been released for weeks.
    """
    pyproject = (REPO / "python" / "pyproject.toml").read_text()
    declared = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    js = json.loads((REPO / "js" / "package.json").read_text())["version"]
    assert captchakraken.__version__ == declared == js, (
        f"captchakraken.__version__={captchakraken.__version__!r}, "
        f"pyproject={declared!r}, js/package.json={js!r}. The two ports ship "
        f"together (rule 1c) and the runtime attribute is the only one a "
        f"caller can read.")


def test_no_export_is_none_in_a_healthy_install():
    """`__init__` degrades seven exports to None when the heavy deps are absent.

    That is deliberate — importing the package must not explode on a
    docs-only install. But it means `hasattr(captchakraken, "CaptchaSolver")`
    is true on an install that cannot solve anything, so a compat check built
    on hasattr would pass on a broken wheel. Assert the value, not the name.
    """
    missing = [n for n in captchakraken.__all__ if getattr(captchakraken, n) is None]
    assert not missing, (
        f"{missing} imported as None — the ModuleNotFoundError guard in "
        f"__init__.py fired, so this environment is missing the client's own "
        f"dependencies. `pip install -e python`.")


def test_a_client_with_no_models_json_still_resolves_a_model(monkeypatch):
    """Clients too old to ship models.json resolve through pinned_model.json.

    models.json arrived after the first releases, so those wheels have no
    registry at all: `prompts._load_registry` returns {} and every `config.*`
    getter has to fall through to the pinned manifest. If that path ever raises,
    an upgrade of THIS repo breaks clients that were already installed — the one
    class of break a published package can never take back.
    """
    monkeypatch.delenv("CAPTCHA_LORA_ADAPTER", raising=False)
    monkeypatch.delenv("CAPTCHA_LORA_NAME", raising=False)
    monkeypatch.delenv("CAPTCHA_LORA_REVISION", raising=False)
    monkeypatch.delenv("CAPTCHA_BASE_MODEL", raising=False)
    monkeypatch.setattr(prompts, "_load_registry", dict)
    prompts.clear_cache()
    config.pinned.cache_clear()
    try:
        assert prompts.registered_models() == {}, "premise: no registry"
        pinned = json.loads((PKG / "pinned_model.json").read_text())
        assert config.lora_adapter() == pinned["lora_adapter"]
        assert config.lora_name() == pinned["lora_name"]
        assert config.base_model() == pinned["base_model"]
        assert config.lora_revision() == pinned["lora_revision"]
        # And it still produces prompts rather than raising: the built-in
        # generation is the floor, not the registry.
        assert prompts.resolve(pinned["lora_adapter"]).version
    finally:
        prompts.clear_cache()
        config.pinned.cache_clear()


def test_an_unregistered_model_name_falls_back_loudly_not_silently(capsys):
    """A `candidate-<run_id>` adapter is not in models.json and has no `/`.

    So registry lookup misses, the Hub step is skipped, and the client answers
    with `latest`'s prompt generation. That mispairing is invisible in a solve
    and looks exactly like a catastrophic model regression — reCAPTCHA 3x3 at
    0.300 against a pinned 0.953 on training run 20260805-002154. It cannot be
    made to work from here (the client has no way to know what a candidate was
    trained on), so the contract is that it WARNS. Silence is the bug.
    """
    prompts.clear_cache()
    ps = prompts.resolve("candidate-20260805-002154")
    warning = capsys.readouterr().err
    assert "not in models.json" in warning and "register it" in warning, (
        f"resolving an unregistered adapter said nothing. It fell back to a "
        f"prompt generation nobody chose, which is the failure that reported "
        f"reCAPTCHA 3x3 at 0.300 against a pinned 0.953. stderr was: "
        f"{warning!r}")
    assert ps.version == prompts.registered_models()[
        prompts.latest_model()]["prompt_version"]
    prompts.clear_cache()


if __name__ == "__main__":
    if "--write" in sys.argv:
        # MERGED, not overwritten: the js/ and mcp/ halves of this file are
        # produced by their own ports and must survive a Python re-snapshot.
        doc = json.loads(CONTRACT.read_text()) if CONTRACT.exists() else {}
        doc.update(surface())
        CONTRACT.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {CONTRACT}")
        subprocess.run(["git", "--no-pager", "diff", "--stat", str(CONTRACT)])
    else:
        print(json.dumps(surface(), indent=2, ensure_ascii=False))
