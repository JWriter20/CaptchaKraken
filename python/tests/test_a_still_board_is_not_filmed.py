"""Speculating on every solve cost the most on the boards it had least to say about.

`_speculate` decides a board is MOVING when two frames hash differently — an
exact SHA-1 over the PNG bytes — so a single pixel of render noise counts as a
second screen. `cycle_closed` then never fires (noise does not repeat), and the
burst runs all the way to `video_burst_max_ms`.

The 6 Sep full Tier 3 (260 attempts) shows it exactly backwards. Genuinely
ANIMATED families closed their cycle at the 4s floor — `tile_flip_video` 3.96s,
`odd_animal_video` 3.98s, `click_highest_jumper` 3.95s — while STILL boards ran
to the 12s ceiling:

    an hCaptcha stacking animation             single_drag   12.03s   0% solved
    an hCaptcha click board          click         11.93s   0% solved
    an hCaptcha missing-piece board           multi_drag    11.89s
    an hCaptcha silhouette-match board        click         11.76s
    an hCaptcha shape-fit board              single_drag    9.96s
    an hCaptcha connect-the-path board            single_drag    9.94s   0% solved

Those are click and drag archetypes with no animation in them. They were not
answered wrongly — the budget went on filming them, and three ran out of it.

And the wall clock is the smaller half: `len(order) >= 2` also DROPS the still
answer and escalates to the keyframe path, so a correct reading of a static
board was thrown away because the render jittered.

So speculation is OPT-IN on both ports. An animated board is still solved —
`animated_probe_enabled` escalates on the repeated-answer signal and
`_record_keyframes` films a bounded 4s — which costs a real animation one extra
round and saves every still board 4-12s.
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


def test_an_unconfigured_solver_does_not_film_the_widget():
    assert PageSolverConfig().speculative_burst_enabled is False
    s = _solver()
    assert s._should_speculate("hcaptcha", text_mode=False) is False
    assert s._should_speculate("unknown", text_mode=False) is False


def test_it_still_films_when_asked_to():
    """Off by default is not the same as removed — the recording path is how
    an animated board is solved at all, and a caller may still want it eagerly."""
    s = _solver(speculative_burst_enabled=True)
    assert s._should_speculate("hcaptcha", text_mode=False) is True


def test_video_solve_disabled_beats_an_explicit_opt_in():
    """One switch turns the whole recording path off. A caller who set both
    means the stricter of the two."""
    s = _solver(speculative_burst_enabled=True, video_solve_enabled=False)
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
    the one line that decides it — `!== true`, so UNSET means off. `=== false`
    would mean unset is ON, which is exactly what was wrong.
    """
    src = JS_SOLVER.read_text()
    m = re.search(r"private shouldSpeculate\([^)]*\)[^{]*\{(.*?)\n  \}", src, re.S)
    assert m, "could not find shouldSpeculate in solver.ts"
    body = m.group(1)
    assert "speculativeBurstEnabled !== true" in body, (
        "the JS port must treat an UNSET speculativeBurstEnabled as OFF "
        "(`!== true`); `=== false` makes unset mean on and the two ports "
        "then measure different drivers")
