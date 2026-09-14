"""A static check because the path is unreachable in the hermetic tier; the slicer sorts frames by name, so an unnamed stem would interleave clips."""

import ast
import inspect
from pathlib import Path

from captchakraken.keyframes import write_keyframes

SRC = Path(__file__).resolve().parents[1] / "src" / "captchakraken"


def _required_kwonly() -> set:
    sig = inspect.signature(write_keyframes)
    return {name for name, p in sig.parameters.items()
            if p.kind is inspect.Parameter.KEYWORD_ONLY
            and p.default is inspect.Parameter.empty}


def _call_sites():
    out = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute) else None)
            if name != "write_keyframes":
                continue
            if any(k.arg is None for k in node.keywords):
                continue
            out.append((path.name, node.lineno,
                        {k.arg for k in node.keywords}))
    return out


def test_the_client_calls_write_keyframes_somewhere():
    assert _call_sites(), "no write_keyframes call sites found — did it move?"


def test_every_call_site_passes_every_required_keyword():
    required = _required_kwonly()
    assert "stem" in required, (
        "write_keyframes no longer requires `stem`; this test is pinning a "
        "signature that has changed — update it deliberately.")

    missing = [
        (fname, lineno, sorted(required - kwargs))
        for fname, lineno, kwargs in _call_sites()
        if required - kwargs
    ]
    assert not missing, (
        "write_keyframes() is missing required keyword-only argument(s) at:\n"
        + "\n".join(f"  {f}:{ln} missing {names}" for f, ln, names in missing)
        + "\nThis raises TypeError the moment that line runs, and on the "
          "animated path it reports as the solver failing to solve.")
