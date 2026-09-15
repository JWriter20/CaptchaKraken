"""Both drivers draw the mouse from the same PCG64 stream, so a seed gives the same path in both and the parity is pinned exactly.

The fixture is recorded from the JS port (`cursory-js`, the same seeds) because Tier 1 has no node.
Never regenerate it to make a failure go away: a failure here means the two drivers started moving differently.
"""
import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cursory_cross_port.json"

# Far looser than the ~2e-12 px of libm residue the port measures, far tighter than a diverged random stream.
_TOL_PX = 1e-9

cursory = pytest.importorskip("cursory", reason="cursory is a runtime dependency of the client")

CASES = json.loads(FIXTURE.read_text())


@pytest.mark.parametrize("case", CASES, ids=[f"seed{c['seed']}" for c in CASES])
def test_python_reproduces_the_js_trajectory(case):
    points, timings = cursory.generate_trajectory(tuple(case["from"]), tuple(case["to"]), case["f"], 1, case["seed"], 0.65)
    assert len(points) == len(case["points"]), "a different sample count is a diverged random stream, not rounding"
    assert len(timings) == len(case["timings"])
    for i, ((x, y), (jx, jy)) in enumerate(zip(points, case["points"])):
        assert abs(x - jx) < _TOL_PX and abs(y - jy) < _TOL_PX, f"sample {i}: python ({x}, {y}) vs js ({jx}, {jy})"
    assert list(timings) == list(case["timings"])


def test_a_seed_is_reproducible_within_one_port():
    a = cursory.generate_trajectory((10, 10), (700, 400), 60, 1, 99, 0.65)
    b = cursory.generate_trajectory((10, 10), (700, 400), 60, 1, 99, 0.65)
    assert a == b


def test_no_seed_means_a_fresh_path_every_time():
    a, _ = cursory.generate_trajectory((10, 10), (700, 400))
    b, _ = cursory.generate_trajectory((10, 10), (700, 400))
    assert a != b


def test_the_endpoints_are_exact():
    for seed in (1, 2, 3):
        pts, _ = cursory.generate_trajectory((123.5, 77.25), (812.75, 455.5), 60, 1, seed, 0.65)
        assert tuple(pts[0]) == (123.5, 77.25) and tuple(pts[-1]) == (812.75, 455.5)
