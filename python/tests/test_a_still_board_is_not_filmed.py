"""Filming a still board must cost it nothing — not its answer, and not its clock.

WHAT THIS ORIGINALLY PINNED, and why it was right at the time. `_speculate`
decided a board was MOVING when two frames hashed differently — an exact SHA-1
over the PNG bytes — so a single pixel of render noise counted as a second
screen. `cycle_closed` then never fired (noise does not repeat) and the burst ran
to `video_burst_max_ms`. The 6 Sep full Tier 3 (260 attempts) shows it exactly
backwards: genuinely ANIMATED families closed their cycle at the 4s floor, while
STILL boards ran to the 12s ceiling —

    an hCaptcha stacking animation             single_drag   12.03s   0% solved
    an hCaptcha click board                    click         11.93s   0% solved
    an hCaptcha missing-piece board            multi_drag    11.89s
    an hCaptcha silhouette-match board         click         11.76s

— and `len(order) >= 2` also DROPPED the still answer, so a correct reading of a
static board was thrown away because the render jittered. Speculation was made
opt-in on both ports, and this file pinned that.

WHAT CHANGED, 2026-09-11. The burst no longer decides on a hash. One recording
now answers static-versus-animated (`classify_by_recording` / its JS twin), with
a settle floor: a board that moves once and stops is called STILL and its answer
STANDS. So the two costs above are gone at the root, and filming while the model
is being asked is free — the recording happens inside a wait the solve was
making anyway. It is now the default, which is the flow the driver is supposed
to have: film and ask at the same time, and throw the film away if the board
turns out to be still.

THE INVARIANT THIS FILE STILL DEFENDS is therefore the one that always mattered,
stated against the new default: a board that does not move must keep its answer
and must not spend the budget proving it is still.
"""
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken.page_solver import PageSolver, PageSolverConfig  # noqa: E402

JS_SOLVER = (Path(__file__).resolve().parents[2] / "js" / "src" / "solver.ts")


def _solver(**kw):
    return PageSolver(config=PageSolverConfig(**kw))


def test_an_unconfigured_solver_films_while_it_asks():
    """ON by default, because the recording is what tells a still board from a
    cycling one — and it runs inside a wait the solve was making anyway."""
    assert PageSolverConfig().speculative_burst_enabled is True
    s = _solver()
    assert s._should_speculate("hcaptcha", text_mode=False) is True
    assert s._should_speculate("unknown", text_mode=False) is True


def test_it_can_still_be_switched_off():
    """On by default is not the same as mandatory. A caller driving a vendor
    that is never animated can spend nothing at all on deciding that."""
    s = _solver(speculative_burst_enabled=False)
    assert s._should_speculate("hcaptcha", text_mode=False) is False


def test_video_solve_disabled_beats_an_explicit_opt_in():
    """One switch turns the whole recording path off. A caller who set both
    means the stricter of the two."""
    s = _solver(speculative_burst_enabled=True, video_solve_enabled=False)  # noqa: E501
    assert s._should_speculate("hcaptcha", text_mode=False) is False


def test_recaptcha_and_text_are_refused_even_when_asked():
    s = _solver(speculative_burst_enabled=True)
    assert s._should_speculate("recaptcha", text_mode=False) is False
    assert s._should_speculate("unknown", text_mode=True) is False


def test_the_js_port_defaults_the_same_way():
    """CLAUDE.md 1c — the two ports must behave identically, and this is the
    kind of default that drifts silently: Tier 3 would go on driving one port
    that films every board and one that films none, and report the average.

    Asserted against the SOURCE rather than by importing the TS, because this
    test has to run in a hermetic Tier 1 with no node toolchain. What it pins is
    the one line that decides it — `=== false`, so UNSET means ON, matching
    `PageSolverConfig.speculative_burst_enabled`. `!== true` would make unset
    mean off, and the two ports would then measure different drivers.
    """
    src = JS_SOLVER.read_text()
    m = re.search(r"private shouldSpeculate\([^)]*\)[^{]*\{(.*?)\n  \}", src, re.S)
    assert m, "could not find shouldSpeculate in solver.ts"
    body = m.group(1)
    assert "speculativeBurstEnabled === false" in body, (
        "the JS port must treat an UNSET speculativeBurstEnabled as ON "
        "(`=== false`); `!== true` makes unset mean off and the two ports "
        "then measure different drivers")
