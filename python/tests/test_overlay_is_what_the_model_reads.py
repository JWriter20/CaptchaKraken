"""`add_overlays_to_image` is exported API, and the model reads what it draws.

This is the one piece of drawing code whose output a customer's accuracy depends
on. The client screenshots a grid, draws numbered labels on it, and sends that
image; the model answers with those numbers because it was never trained to
invent a numbering. Get the drawing wrong and nothing errors — reCAPTCHA 4x4
simply scores zero and looks like a broken model.

So these pin what the function PRODUCES, not that it ran: where marks land, how
the two accepted bbox forms are told apart, which file gets written, and what
happens on input it cannot use.
"""

import numpy as np
import pytest
from PIL import Image

from captchakraken import add_overlays_to_image


def _blank(path, size=(400, 300), color=(255, 255, 255)):
    Image.new("RGB", size, color).save(path)
    return str(path)


def _pixels(path):
    return np.array(Image.open(path).convert("RGB"))


def _changed_mask(before, after):
    """Pixels that differ at all between two same-shape images."""
    return np.any(before != after, axis=2)


def test_a_normalised_box_is_drawn_where_it_was_asked_for(tmp_path):
    """`bbox` of all-<=1.0 values means [x1, y1, x2, y2] as fractions of the image.

    The consequence of reading these as pixels instead would be every label
    crammed into the top-left corner, which is exactly what a 1x1 crop of the
    board looks like to the model: unreadable, and answered wrong.
    """
    src = _blank(tmp_path / "in.png")
    before = _pixels(src)
    out = str(tmp_path / "out.png")

    add_overlays_to_image(src, [{"bbox": [0.5, 0.5, 0.9, 0.9], "number": 7}], output_path=out)

    changed = _changed_mask(before, _pixels(out))
    assert changed.any(), "nothing was drawn at all"

    ys, xs = np.nonzero(changed)
    # The box spans x 200..360 and y 150..270 on a 400x300 image. Allow a margin
    # for stroke width and the label, but the marks must live in that half.
    assert xs.min() >= 150, f"drawing began at x={xs.min()}, far left of the requested box"
    assert ys.min() >= 100, f"drawing began at y={ys.min()}, far above the requested box"
    assert xs.max() <= 399 and ys.max() <= 299


def test_a_pixel_box_is_read_as_x_y_width_height_not_as_corners(tmp_path):
    """The legacy form is [x, y, w, h]. Reading it as [x1, y1, x2, y2] would draw
    a box ending at w — a wildly different rectangle, silently."""
    src = _blank(tmp_path / "in.png")
    before = _pixels(src)
    out = str(tmp_path / "out.png")

    # As w/h this is x 300..380. As corners it would be x 300..40, i.e. empty or
    # inverted, and the right-hand edge would never be drawn.
    add_overlays_to_image(src, [{"bbox": [300, 100, 80, 80]}], output_path=out)

    changed = _changed_mask(before, _pixels(out))
    xs = np.nonzero(changed)[1]
    assert xs.max() >= 370, "the right edge of an [x, y, w, h] box was not drawn at x+w"


def test_the_source_is_left_alone_when_an_output_path_is_given(tmp_path):
    """A caller that overlays a screenshot must still have the screenshot.

    The solver keeps the clean capture to compare frames against; overwriting it
    would make the wait gate compare a board against a picture of itself with
    boxes drawn on.
    """
    src = _blank(tmp_path / "in.png")
    before = _pixels(src)
    out = str(tmp_path / "out.png")

    add_overlays_to_image(src, [{"bbox": [0.1, 0.1, 0.4, 0.4], "number": 1}], output_path=out)

    assert np.array_equal(before, _pixels(src)), "the source image was modified"
    assert not np.array_equal(before, _pixels(out)), "the output image was not drawn on"


def test_no_output_path_overwrites_the_source(tmp_path):
    """The documented default, and the one the CLI relies on."""
    src = _blank(tmp_path / "in.png")
    before = _pixels(src)

    add_overlays_to_image(src, [{"bbox": [0.1, 0.1, 0.4, 0.4], "number": 1}])

    assert not np.array_equal(before, _pixels(src))


def test_an_empty_box_list_still_produces_a_readable_image(tmp_path):
    """A board with nothing to mark is a normal outcome, not an error.

    It must still write an image the next stage can open, rather than half a
    file or none.
    """
    src = _blank(tmp_path / "in.png")
    out = str(tmp_path / "out.png")

    add_overlays_to_image(src, [], output_path=out)

    assert np.array_equal(_pixels(src), _pixels(out))


def test_the_result_is_rgb_so_a_later_jpeg_save_cannot_fail(tmp_path):
    """Drawing happens in RGBA; saving must convert back.

    A leftover alpha channel raises "cannot write mode RGBA as JPEG" deep in the
    next stage, a long way from the overlay that caused it.
    """
    src = _blank(tmp_path / "in.png")
    out = str(tmp_path / "out.png")

    add_overlays_to_image(src, [{"bbox": [0.2, 0.2, 0.6, 0.6], "text": "cars"}], output_path=out)

    with Image.open(out) as img:
        assert img.mode == "RGB"


def test_a_box_without_a_bbox_raises_instead_of_writing_a_wrong_overlay(tmp_path):
    """Fail loudly. A silently skipped box means the model is asked about a grid
    whose numbering does not match the one it is shown."""
    src = _blank(tmp_path / "in.png")

    with pytest.raises(KeyError):
        add_overlays_to_image(src, [{"number": 3}], output_path=str(tmp_path / "out.png"))


def test_a_missing_source_image_raises(tmp_path):
    """The caller passed a path that is not there; there is no sane overlay to
    produce and pretending otherwise hides the real failure."""
    with pytest.raises(Exception):
        add_overlays_to_image(
            str(tmp_path / "nope.png"), [{"bbox": [0.1, 0.1, 0.2, 0.2]}],
            output_path=str(tmp_path / "out.png"),
        )
