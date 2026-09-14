from __future__ import annotations

import ast
from pathlib import Path

SOLVER = (Path(__file__).resolve().parents[1]
          / "src" / "captchakraken" / "page_solver.py")

LOOKUP_FN = "_get_verify_button"


def _module() -> ast.Module:
    return ast.parse(SOLVER.read_text())


def _calls_to(node: ast.AST, name: str) -> list:
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == name]


def _action_loops(tree: ast.Module) -> list:
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        it = node.iter
        name = (getattr(it, "id", None)
                or getattr(getattr(it, "attr", None), "__str__", lambda: None)()
                or getattr(it, "attr", None))
        if isinstance(name, str) and "action" in name.lower():
            out.append(node)
    return out


def test_the_submit_control_is_resolved_outside_the_action_loop():
    tree = _module()
    loops = _action_loops(tree)
    assert loops, (
        "no `for ... in <actions>` loop found in page_solver.py — this test "
        "cannot pin what it was written to pin; re-point it at the loop that "
        "executes the model's plan")

    nested = [c for loop in loops for c in _calls_to(loop, LOOKUP_FN)]
    assert not nested, (
        f"{LOOKUP_FN} is called INSIDE the loop over the model's actions "
        f"(line{'s' if len(nested) > 1 else ''} "
        f"{', '.join(str(c.lineno) for c in nested)}).\n\n"
        "A plan with NO actions never enters that loop, so no submit control is "
        "resolved, `should_submit` finds verify_button None, nothing is pressed "
        "and the solve aborts on 'performed no interactions'. That is the exact "
        "shape of reCAPTCHA 3x3's `none_present` variation, whose correct answer "
        "is to select nothing and press SKIP.\n\n"
        "Resolve the control after the loop, on the same level as the submit "
        "decision that consumes it."
    )


def test_the_lookup_still_happens_at_all():
    assert _calls_to(_module(), LOOKUP_FN), (
        f"{LOOKUP_FN} is never called in page_solver.py — the widget's own "
        "submit control would never be pressed by any path")
