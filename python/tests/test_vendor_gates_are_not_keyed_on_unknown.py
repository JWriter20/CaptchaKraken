"""Two behaviours must not be gated on the literal vendor string `unknown`.

`puzzle_source` is overloaded. It is the grid hint, and it is ALSO what decides:

  * `text_mode` — whether this widget wants a typed string. MTCaptcha, Yandex and
    BotDetect are the typed captchas, and all three report `unknown` today.
  * the settle / animated probe — whether to wait for a board that is still
    cycling. GeeTest's svg board changes its glyph set, and Tencent animates too;
    both report `unknown` today.

Both are logically "not hCaptcha and not reCAPTCHA" — the comments beside them
say exactly that, and neither vendor has ever served a typed or animated
challenge. Written as `== "unknown"` the two are the same test only while
`unknown` is the sole third value.

The moment anyone reports a vendor by NAME — the obvious next step being to
constrain which grid shapes each vendor may be solved as, since most of them ship
no grid at all — the literal comparison stops matching and BOTH behaviours switch
off for the named vendor. Nothing raises. The typed captcha simply stops being
recognised as typed, and the animated board is screenshotted mid-cycle and
answered from whatever single frame was caught. That is the bug the settle probe
was added to fix, re-introduced by a change that looks unrelated to it.

So the gate is a named set, and this test is what stops the literal coming back —
in BOTH ports, because they have to behave the same (CLAUDE.md 1c) and because
the JS half is where the vendor is actually decided.
"""
import os
import re
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_PY = os.path.join(os.path.dirname(__file__), "..", "src", "captchakraken", "page_solver.py")
_JS = os.path.join(os.path.dirname(__file__), "..", "..", "js", "src", "solver.ts")

#: `puzzle_source == "unknown"` / `puzzleSource === 'unknown'`, either quote style
#: and either direction of the comparison.
_LITERAL = re.compile(
    r"""puzzle_?[Ss]ource\s*[=!]==?\s*['"]unknown['"]|['"]unknown['"]\s*[=!]==?\s*puzzle_?[Ss]ource"""
)


def _source(path):
    if not os.path.isfile(path):
        pytest.skip(f"port not present in this checkout: {path}")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize("path,port", [(_PY, "python"), (_JS, "js")])
def test_no_behaviour_is_gated_on_the_literal_unknown(path, port):
    hits = [m.group(0) for m in _LITERAL.finditer(_source(path))]
    assert not hits, (
        f"{port}: {len(hits)} comparison(s) against the literal 'unknown' "
        f"({hits}). Use VENDORS_WITH_BESPOKE_HANDLING — naming any vendor turns "
        f"these off silently, and MTCaptcha/Yandex/BotDetect are typed captchas "
        f"while GeeTest/Tencent need the settle probe."
    )


@pytest.mark.parametrize("path,port", [(_PY, "python"), (_JS, "js")])
def test_both_ports_name_the_same_two_vendors(path, port):
    src = _source(path)
    assert "VENDORS_WITH_BESPOKE_HANDLING" in src, f"{port}: gate set is missing"
    block = src[src.index("VENDORS_WITH_BESPOKE_HANDLING"):][:400]
    for vendor in ("hcaptcha", "recaptcha"):
        assert vendor in block, f"{port}: {vendor} is not in the bespoke set"


def test_the_python_gate_is_exactly_the_big_two():
    from captchakraken.page_solver import VENDORS_WITH_BESPOKE_HANDLING

    assert VENDORS_WITH_BESPOKE_HANDLING == frozenset({"hcaptcha", "recaptcha"}), (
        "adding a vendor here silently disables typed-challenge detection and the "
        "animated settle probe for it"
    )
