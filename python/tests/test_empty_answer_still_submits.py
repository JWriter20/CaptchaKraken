"""An empty answer is an answer, and it still has to be sent.

Regression: the submit-control lookup lived INSIDE the loop over the model's
actions, while the decision to press it lived outside. So a plan with no actions
never resolved a control, and `should_submit` — which explicitly wants to press
"when we had nothing to do and want the round to advance" — found `verify_button`
still None and pressed nothing. `performed_action` stayed False, and the caller's
"still detected but the solver performed no interactions" guard aborted the
solve.

WHERE IT BIT, AND WHY IT LOOKED LIKE A MODEL REGRESSION

reCAPTCHA's 3x3 has a `none_present` variation: the prompt names a class, no
tile contains it, and the widget's control reads SKIP rather than VERIFY. The
correct answer is to select nothing and press it. Fixture seed 20260730 is
exactly that — `target_class: "traffic light"`, `target_ids: []`,
`submit_label: "SKIP"`.

It surfaced when CaptchaKrakenFinetune fixed font resolution on the macOS Tier 3
runner. Before that fix the prompt rendered in a fallback bitmap face and read
"Selectall images with / traffic lights"; after it, the widget draws real
reCAPTCHA chrome with the target term bolded and a legible "If there are none,
click skip." The client runs at temperature 0, so the model is a function of the
picture: a correct picture got a correct empty answer, and the empty answer is
the one shape the driver could not send. `recaptcha_grid_3x3` js went 2/3 -> 1/3
and read as the font fix causing a regression.

`getVerifyButton` was never the problem — "Skip" has always been in its list.
The finder was simply never called.

This is a STRUCTURAL test. What is wrong is where a call sits relative to a
loop, and no amount of mocking `_execute_plan` (200 lines, a screenshot, a
planner round-trip and a live page) observes that as directly as reading the
tree. The JS half is pinned by `js/src/empty-answer-submits.test.ts`; per
CLAUDE.md 1c the two ports must behave the same.
"""
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
    """Every `for ... in <something action-ish>` in the module.

    Matched by the iterated name rather than by line number, so the test keeps
    pointing at the right loop when the file moves.
    """
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
    """Guard the obvious over-correction: deleting the call also passes above."""
    assert _calls_to(_module(), LOOKUP_FN), (
        f"{LOOKUP_FN} is never called in page_solver.py — the widget's own "
        "submit control would never be pressed by any path")
