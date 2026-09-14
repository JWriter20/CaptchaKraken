"""Unset means on, via `is False` rather than `is not True`; idle wander must be off during the burst (one live burst filmed a dozen screens made by the mouse); reCAPTCHA and text never speculate."""

import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from captchakraken.page_solver import PageSolver, PageSolverConfig

JS_SOLVER = (Path(__file__).resolve().parents[2] / "js" / "src" / "solver.ts")


def _solver(**kw):
    return PageSolver(config=PageSolverConfig(**kw))


def test_an_unconfigured_solver_films_while_it_asks():
    assert PageSolverConfig().speculative_burst_enabled is True
    s = _solver()
    assert s._should_speculate("hcaptcha", text_mode=False) is True
    assert s._should_speculate("unknown", text_mode=False) is True


def test_it_can_still_be_switched_off():
    s = _solver(speculative_burst_enabled=False)
    assert s._should_speculate("hcaptcha", text_mode=False) is False


def test_video_solve_disabled_beats_an_explicit_opt_in():
    s = _solver(speculative_burst_enabled=True, video_solve_enabled=False)
    assert s._should_speculate("hcaptcha", text_mode=False) is False


def test_recaptcha_and_text_are_refused_even_when_asked():
    s = _solver(speculative_burst_enabled=True)
    assert s._should_speculate("recaptcha", text_mode=False) is False
    assert s._should_speculate("unknown", text_mode=True) is False


def test_the_js_port_defaults_the_same_way():
    src = JS_SOLVER.read_text()
    m = re.search(r"private shouldSpeculate\([^)]*\)[^{]*\{(.*?)\n  \}", src, re.S)
    assert m, "could not find shouldSpeculate in solver.ts"
    body = m.group(1)
    assert "speculativeBurstEnabled === false" in body, (
        "the JS port must treat an UNSET speculativeBurstEnabled as ON "
        "(`=== false`); `!== true` makes unset mean off and the two ports "
        "then measure different drivers")
