"""The metric the driver holds the mouse on, for animated challenges.

The model answers an animated puzzle with a frame NUMBER, and the driver then
waits until the live widget looks like that frame around the click point before
pressing down. `region_box` picks the neighbourhood and `region_diff_ratio`
scores it. Both are shared with the keyframe slicing, so the label and the gate
agree by construction.

Every failure mode here is silent: a box that covers nothing scores a perfect
match and the gate opens on any state at all; a metric that ignores the box
opens on movement elsewhere on the board; a metric that returns "same" for two
different shapes opens on a resized screenshot.
"""

import numpy as np

from captchakraken.keyframes import frame_diff_ratio, region_box, region_diff_ratio


def _frame(w=200, h=120, value=10):
    return np.full((h, w, 3), value, dtype=np.uint8)


def test_a_point_on_the_edge_still_gets_a_box_with_pixels_in_it():
    """A target flush against the border would otherwise clamp to zero area, and
    a comparison over no pixels reads as a perfect match — the gate would open
    immediately, on any frame."""
    for point in [(0.0, 0.0), (1.0, 1.0), (0.0, 1.0), (1.0, 0.0)]:
        x1, y1, x2, y2 = region_box((200, 120), point)
        assert x2 > x1 and y2 > y1, f"empty box for point {point}"


def test_a_box_never_leaves_the_frame():
    """Cropping outside the array silently yields a smaller slice on one side and
    an empty one on the other, so the two crops stop being comparable."""
    for point in [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)]:
        x1, y1, x2, y2 = region_box((200, 120), point)
        assert 0 <= x1 < x2 <= 200
        assert 0 <= y1 < y2 <= 120


def test_identical_frames_score_zero():
    a = _frame()
    assert frame_diff_ratio(a, a.copy()) == 0.0
    assert region_diff_ratio(a, a.copy(), region_box((200, 120), (0.5, 0.5))) == 0.0


def test_two_shapes_that_do_not_match_read_as_completely_different():
    """A resized live screenshot must not be scored as a crop of a different
    geometry. 1.0 means "nothing matches", which keeps the gate closed."""
    assert frame_diff_ratio(_frame(200, 120), _frame(100, 60)) == 1.0
    assert region_diff_ratio(_frame(200, 120), _frame(100, 60)) == 1.0


def test_a_missing_frame_reads_as_completely_different():
    """A screenshot that failed to decode must keep the gate shut rather than
    open it."""
    assert frame_diff_ratio(None, _frame()) == 1.0
    assert region_diff_ratio(_frame(), None) == 1.0


def test_the_region_gate_ignores_change_outside_its_box():
    """This is the whole reason the gate is regional. A cycling board is moving
    everywhere; only the neighbourhood of the click point has to settle."""
    a = _frame()
    b = a.copy()
    b[0:20, 0:20] = 250  # a loud change in the far corner

    box = region_box((200, 120), (0.9, 0.9))
    assert region_diff_ratio(a, b, box) == 0.0, "change outside the box moved the score"
    assert frame_diff_ratio(a, b) > 0.0, "the whole-frame metric should see it"


def test_the_region_gate_sees_change_inside_its_box():
    a = _frame()
    b = a.copy()
    x1, y1, x2, y2 = region_box((200, 120), (0.5, 0.5))
    b[y1:y2, x1:x2] = 250

    assert region_diff_ratio(a, b, (x1, y1, x2, y2)) == 1.0


def test_a_change_smaller_than_the_threshold_is_not_movement():
    """The metric counts pixels differing by more than 30 in a channel. Codec
    noise and antialiasing sit under that; treating them as movement would mean
    the gate never opens on a board that has already settled."""
    a = _frame(value=10)
    b = _frame(value=35)  # a delta of 25, under the threshold
    assert frame_diff_ratio(a, b) == 0.0

    c = _frame(value=100)  # a delta of 90, over it
    assert frame_diff_ratio(a, c) == 1.0
