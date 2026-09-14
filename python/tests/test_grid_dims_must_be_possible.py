import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captchakraken.solver import _grid_dims


@pytest.mark.parametrize("n", [0, 1, 4, 6, 8, 10, 12, 15, 20, 25])
def test_a_count_no_registered_puzzle_has_is_not_a_grid(n):
    for source in ("hcaptcha", "recaptcha", "unknown"):
        assert _grid_dims(n, source) is None, (
            f"{n} cells accepted as a grid for {source} — no registered puzzle "
            f"has {n} cells, so this can only be a false positive"
        )


def test_hcaptcha_never_has_sixteen_cells():
    assert _grid_dims(16, "hcaptcha") is None


def test_hcaptcha_still_solves_its_own_3x3():
    assert _grid_dims(9, "hcaptcha") == (3, 3)


def test_recaptcha_keeps_both_of_its_boards():
    assert _grid_dims(9, "recaptcha") == (3, 3)
    assert _grid_dims(16, "recaptcha") == (4, 4)


def test_an_unknown_vendor_is_allowed_both_shapes():
    assert _grid_dims(9, "unknown") == (3, 3)
    assert _grid_dims(16, "unknown") == (4, 4)


def test_an_unrecognised_vendor_string_is_treated_as_unknown():
    assert _grid_dims(9, "turnstile") == (3, 3)
    assert _grid_dims(16, "turnstile") == (4, 4)


def test_solve_grid_cannot_invent_its_own_dimensions():
    import inspect

    from captchakraken.solver import CaptchaSolver

    params = inspect.signature(CaptchaSolver._solve_grid).parameters
    assert "rows" in params and "cols" in params, (
        "_solve_grid derives its own dimensions again — the sqrt fallback is "
        "what turned a 12-cell false positive into a 4x3 board"
    )
    src = inspect.getsource(CaptchaSolver._solve_grid)
    assert "math.sqrt" not in src, "_solve_grid still computes dimensions from sqrt"
