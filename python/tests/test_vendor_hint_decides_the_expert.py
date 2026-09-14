import pytest

from captchakraken.page_solver import vendor_from_src
from captchakraken.solver import _grid_dims

REAL_CHALLENGE = ("https://newassets.hcaptcha.com/captcha/v1/3f1a2b/static/"
                  "hcaptcha.html#frame=challenge&id=0x1&host=example.com")
REAL_CHECKBOX = ("https://newassets.hcaptcha.com/captcha/v1/3f1a2b/static/"
                 "hcaptcha.html#frame=checkbox&id=0x1&host=example.com")
FIXTURE_CHALLENGE = ("http://127.0.0.1:8080/frame?"
                     "hcaptcha.html#frame=challenge")


def test_the_vendor_challenge_is_hcaptcha():
    assert vendor_from_src(REAL_CHALLENGE) == "hcaptcha"
    assert vendor_from_src(REAL_CHECKBOX) == "hcaptcha"


def test_an_hcaptcha_frame_not_served_off_the_apex_host_is_still_hcaptcha():
    assert vendor_from_src(FIXTURE_CHALLENGE) == "hcaptcha"


def test_recaptcha_is_named_by_its_api2_path():
    assert vendor_from_src("https://www.google.com/recaptcha/api2/bframe?k=6Le") == "recaptcha"
    assert vendor_from_src("https://www.google.com/recaptcha/api2/anchor?k=6Le") == "recaptcha"
    assert vendor_from_src("https://recaptcha.net/recaptcha/api2/bframe?k=6Le") == "recaptcha"


def test_recaptcha_does_not_read_as_hcaptcha():
    assert vendor_from_src("/recaptcha/api2/bframe") == "recaptcha"


@pytest.mark.parametrize("src", ["https://api.geetest.com/gt.js", ".yidun_panel", "", None])
def test_anything_else_is_unknown(src):
    assert vendor_from_src(src) == "unknown"


def test_naming_hcaptcha_refuses_a_lattice_it_does_not_ship():
    assert _grid_dims(16, "hcaptcha") is None, \
        "hCaptcha ships no 4x4 — a 16-cell detection there is find_grid on the bands"
    assert _grid_dims(9, "hcaptcha") == (3, 3), "an hCaptcha 3x3 is a real grid"


def test_failing_to_name_the_vendor_lets_that_same_lattice_through():
    assert _grid_dims(16, "unknown") == (4, 4), (
        "if this ever starts refusing, the pairing below is no longer the "
        "reason the mis-route mattered — re-read the finding before relaxing it")
    assert _grid_dims(16, vendor_from_src(FIXTURE_CHALLENGE)) is None, (
        "the fixture's hCaptcha board is being allowed a 4x4 again — the shape "
        "gate is off and find_grid's false positives reach the grid expert")


def test_recaptcha_keeps_both_of_its_shapes():
    assert _grid_dims(9, "recaptcha") == (3, 3)
    assert _grid_dims(16, "recaptcha") == (4, 4)
