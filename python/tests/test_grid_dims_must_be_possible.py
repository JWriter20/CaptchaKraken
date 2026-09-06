"""A detection is only a grid if it is a grid we actually ship.

`find_grid` proposes lattices; it does not know what a captcha vendor sells. It
allows rectangles and any dimension from MIN_GRID_DIM up, so a 12-cell or
20-cell "grid" is a perfectly ordinary thing for it to return. `_solve_grid`
then used to accept whatever arrived: 9 became 3x3, 16 became 4x4, and ANY OTHER
COUNT was turned into dimensions with `cols = int(sqrt(n)); rows = ceil(n/cols)`.
A 12-cell false positive became a 4x3 board, was stamped with twelve numbers,
and was answered with cell ids for a puzzle that has no cells.

That is strictly more permissive than the pipeline that produces the training
data: `grid_overlay.detect_cells` refuses anything but 9 or 16, so no such board
can exist in the corpus. The model was never shown a 4x3 and never will be.

Two independent guards, because they catch different things:

  * SHAPE — 9 and 16 are the only cell counts any registered grid puzzle has.
    Everything else is impossible by construction, whatever the vendor.
  * VENDOR — of the registered grid types, only reCAPTCHA ships a 4x4. hCaptcha
    ships one grid puzzle and it is 3x3, so a 16-cell detection on an hCaptcha
    board is not a hard call, it is a contradiction. Measured on real captures:
    that single rule removes four of the five boards that currently clear both
    `find_grid` and `_is_real_grid` and would be answered as grids
    (`hcaptcha_drag_missing_slot`, three variations).

`unknown` stays permissive, deliberately and in both directions. It is what the
driver reports for every vendor that is not hCaptcha or reCAPTCHA — GeeTest and
Prosopo among them, which DO ship 3x3 grids — and it is also what the one-shot
image API defaults to, which is how the offline grader runs. Narrowing `unknown`
would silently stop scoring reCAPTCHA 4x4 in evaluation while looking like a
safety improvement.
"""
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captchakraken.solver import _grid_dims  # noqa: E402


@pytest.mark.parametrize("n", [0, 1, 4, 6, 8, 10, 12, 15, 20, 25])
def test_a_count_no_registered_puzzle_has_is_not_a_grid(n):
    """Whatever the vendor. These are the counts the sqrt fallback used to
    silently turn into a rectangle."""
    for source in ("hcaptcha", "recaptcha", "unknown"):
        assert _grid_dims(n, source) is None, (
            f"{n} cells accepted as a grid for {source} — no registered puzzle "
            f"has {n} cells, so this can only be a false positive"
        )


def test_hcaptcha_never_has_sixteen_cells():
    """The measured false positive: hCaptcha ships no 4x4."""
    assert _grid_dims(16, "hcaptcha") is None


def test_hcaptcha_still_solves_its_own_3x3():
    """hcaptcha_grid_3x3_property is a registered grid type and must survive —
    the CLI's own help text claims 'hCaptcha skips grid detection', which would
    have broken it."""
    assert _grid_dims(9, "hcaptcha") == (3, 3)


def test_recaptcha_keeps_both_of_its_boards():
    assert _grid_dims(9, "recaptcha") == (3, 3)
    assert _grid_dims(16, "recaptcha") == (4, 4)


def test_an_unknown_vendor_is_allowed_both_shapes():
    """GeeTest and Prosopo report as `unknown` and ship 3x3s; the one-shot image
    API defaults to `unknown` and is how 4x4s are graded offline."""
    assert _grid_dims(9, "unknown") == (3, 3)
    assert _grid_dims(16, "unknown") == (4, 4)


def test_an_unrecognised_vendor_string_is_treated_as_unknown():
    """Fail OPEN on a vendor nobody has taught this table about: refusing would
    make a new vendor's real grid unsolvable, and the shape guard still holds."""
    assert _grid_dims(9, "turnstile") == (3, 3)
    assert _grid_dims(16, "turnstile") == (4, 4)


def test_solve_grid_cannot_invent_its_own_dimensions():
    """The structural half. `_solve_grid` takes the shape it was cleared for
    rather than deriving one, so there is no code path left that can build a
    lattice the vendor does not ship."""
    import inspect

    from captchakraken.solver import CaptchaSolver

    params = inspect.signature(CaptchaSolver._solve_grid).parameters
    assert "rows" in params and "cols" in params, (
        "_solve_grid derives its own dimensions again — the sqrt fallback is "
        "what turned a 12-cell false positive into a 4x3 board"
    )
    src = inspect.getsource(CaptchaSolver._solve_grid)
    assert "math.sqrt" not in src, "_solve_grid still computes dimensions from sqrt"
