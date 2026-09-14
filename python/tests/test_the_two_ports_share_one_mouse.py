"""The two drivers no longer RESEMBLE each other's mouse. They are the same one.

CLAUDE.md 1c says the Python and JS clients must behave identically. For every
other shared surface that means two implementations and a test that they agree.
For the mouse it used to mean the same Bezier model written twice, pinned
statistically — which is the best two independent implementations can manage,
because a curve written twice will differ in the last bits and then diverge over
a thousand samples.

`cursory` and `cursory-js` are a PORT rather than a rewrite. Both reduce to the
same numpy PCG64 stream, so a seed produces the same trajectory in both, and the
parity that used to be statistical is now exact. That is a stronger property
than we could previously state at all, so it is pinned exactly.

The fixture is recorded from the JS port — `node -e` against `dist/trajectory.js`
— and checked against Python here, because Tier 1 is hermetic and has no node.
Regenerate it the same way if the cases ever need to change; do NOT regenerate it
to make a failure go away, because a failure here means the two drivers have
started moving the mouse differently, which is exactly what it is for.

The tolerance is 1e-9 px rather than zero. Cursory's own port documents ~2e-12 px
of residue from `exp`, `atan2`, `sin` and `cos` rounding differently in V8 than
in the C library CPython calls; a seed that drew even one different RANDOM number
would give a different trajectory rather than a slightly different one, so this
tolerance cannot hide the failure it is there to survive.
"""
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cursory_cross_port.json"

#: Far looser than the ~2e-12 px the port measures, far tighter than any
#: difference a diverging random stream could produce.
_TOL_PX = 1e-9

pytest.importorskip("cursory", reason="cursory is a runtime dependency of the client")

from captchakraken.trajectory import generate_trajectory  # noqa: E402

CASES = json.loads(FIXTURE.read_text())


@pytest.mark.parametrize("case", CASES, ids=[f"seed{c['seed']}" for c in CASES])
def test_python_reproduces_the_js_trajectory(case):
    points, timings = generate_trajectory(
        case["from"], case["to"], case["f"], 1.0, case["seed"], 0.65)

    assert len(points) == len(case["points"]), (
        f"the two ports disagree on how many samples this movement has "
        f"({len(points)} here, {len(case['points'])} in JS) — that is a diverged "
        f"random stream, not a rounding difference")
    assert len(timings) == len(case["timings"])

    for i, ((x, y), (jx, jy)) in enumerate(zip(points, case["points"])):
        assert abs(x - jx) < _TOL_PX and abs(y - jy) < _TOL_PX, (
            f"sample {i} differs: python ({x}, {y}) vs js ({jx}, {jy})")

    for i, (t, jt) in enumerate(zip(timings, case["timings"])):
        assert t == jt, f"timing {i} differs: python {t} vs js {jt}"


def test_a_seed_is_reproducible_within_one_port():
    a = generate_trajectory((10, 10), (700, 400), 60, 1.0, 99, 0.65)
    b = generate_trajectory((10, 10), (700, 400), 60, 1.0, 99, 0.65)
    assert a == b


def test_no_seed_means_a_fresh_path_every_time():
    """Production never passes a seed. Two calls must not produce one signature."""
    a, _ = generate_trajectory((10, 10), (700, 400))
    b, _ = generate_trajectory((10, 10), (700, 400))
    assert a != b, "unseeded movements repeat — every solve would share one path"


def test_the_endpoints_are_exact():
    """Texture in the middle, exactness at the ends: a click lands on the last
    sample, so it has to be the pixel the caller asked for."""
    for seed in (3, 4, 5):
        pts, _ = generate_trajectory((123.5, 77.25), (812.75, 455.5), 60, 1.0, seed, 0.65)
        assert pts[0] == (123.5, 77.25)
        assert pts[-1] == (812.75, 455.5)
