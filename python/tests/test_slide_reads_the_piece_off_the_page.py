"""Where the piece IS, asked of the page — not inferred from a pixel diff.

The CV fallback measures the piece as the union of everything that changed
between two frames: the ground it vacated plus where it is now. Its edges are
inset by however much the piece fades into the board, and that inset cancels out
of the RATIO but not out of the WIDTH. `piece_center = right_edge - width / 2`
then turns an under-measured width into a centre too far RIGHT by half the
error — so the loop reports itself converged while the piece is short of the
notch.

MEASURED on gt4.geetest.com's slide demo, 2026-09-12:

    .geetest_slice is 80x80 in the DOM
    the diff inferred 63px and 74px on two consecutive live attempts
    63 puts the centre 8.5px right of truth -> releases 8.5px short
    8.5px on a 340px widget is 2.5%; the notch accepts about 2%

Across two filmed runs of ten attempts, every one of the fourteen refused drags
undershot and not one overshot — the signature of a bias, not of noise.

The JS half is js/src/slide-reads-the-piece-off-the-page.test.ts.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from captchakraken.page_solver import PageSolver, PageSolverConfig    # noqa: E402

BOX = {"x": 0.0, "y": 0.0, "width": 340.0, "height": 400.0}
TARGET = 245.0
TRUE_PIECE_W = 80.0
DIFF_PIECE_W = 63.0      # what the diff under-measures it as, live
LEFT_INSET = TRUE_PIECE_W - DIFF_PIECE_W


class _Handle:
    def __init__(self, state):
        self.state = state

    def bounding_box(self):
        return {"x": self.state["handle_x"] - 25, "y": 300, "width": 50, "height": 40}


class _Piece:
    def __init__(self, state):
        self.state = state

    def bounding_box(self):
        c = self.state["piece_centre"]()
        return {"x": c - TRUE_PIECE_W / 2, "y": 100,
                "width": TRUE_PIECE_W, "height": TRUE_PIECE_W}


def _run(piece_in_dom: bool) -> float:
    """Drive `_execute_slide` and return the piece's final centre."""
    state = {"handle_x": 40.0}
    state["piece_centre"] = lambda: TRUE_PIECE_W / 2 + (state["handle_x"] - 40.0)

    s = PageSolver(config=PageSolverConfig())

    def find_control(scope, selectors):
        if any("btn" in x or "slider" in x or "handle" in x for x in selectors):
            return _Handle(state)
        return _Piece(state) if piece_in_dom else None

    def smooth_move(page, x, y):
        state["handle_x"] = x

    def track_piece(element, before, after, exclude=None, travel=0.0):
        # The asymmetry the live probes imply: the piece's CURRENT RIGHT edge
        # reads sharp, the VACATED LEFT edge is inset by the full 17px. A
        # symmetric inset would cancel out of `right - width/2`; this does not.
        orig_left = TRUE_PIECE_W / 2 - TRUE_PIECE_W / 2
        # `piece: None`: this is the vendor whose piece the CV cannot separate
        # from the ground it vacated, which is the whole point of the test.
        return {"bbox": [orig_left + LEFT_INSET, 0.0,
                         state["piece_centre"]() + TRUE_PIECE_W / 2, TRUE_PIECE_W],
                "piece": None}

    class _NullHuman:
        def press(self, page): pass
        def release(self, page): pass
        def pause(self, kind): pass

    # The piece is measured through `_measure_piece_box`, which takes EVERY
    # match of each selector and keeps the first that could be a piece —
    # `_find_control`'s first-visible-hit is right for the handle and wrong for
    # the piece.
    def measure_piece_box(scope, widget_width):
        if not piece_in_dom:
            return None
        return {"x": state["piece_centre"]() - TRUE_PIECE_W / 2, "y": 100.0,
                "width": TRUE_PIECE_W, "height": TRUE_PIECE_W}

    s._measure_piece_box = measure_piece_box          # type: ignore
    s._find_control = find_control                    # type: ignore
    s._smooth_move = smooth_move                      # type: ignore
    s._track_piece = track_piece                      # type: ignore
    s._shot_scale = lambda shot, css_w: 1.0           # type: ignore
    s._move = lambda *a, **k: None                    # type: ignore
    s._human = _NullHuman()                           # type: ignore
    s._screenshot = lambda *a, **k: None              # type: ignore

    class _El:
        def screenshot(self, **kw): pass

    s._execute_slide(
        None, _El(), None,
        {"action": "drag",
         "target_bounding_box": [TARGET / BOX["width"], 0.4, TARGET / BOX["width"], 0.5]},
        BOX,
    )
    return state["piece_centre"]()


def test_with_the_piece_in_the_dom_the_drag_lands_on_the_notch():
    err = _run(piece_in_dom=True) - TARGET
    assert abs(err) <= 2.0, f"should seat within the notch, landed {err:.1f}px off"


def test_the_diff_only_path_is_what_undershoots_and_is_still_the_fallback():
    """Not an aspiration — the behaviour measured live, reproduced so the fix is
    pinned against the thing it fixes. A vendor that draws its piece into a
    canvas still gets this path and still solves; it is simply less accurate,
    which is why the DOM reading is preferred when it exists."""
    err = _run(piece_in_dom=False) - TARGET
    assert err < -4.0, f"expected the known undershoot, got {err:.1f}px"
    assert abs(err + LEFT_INSET / 2) < 1.5, (
        f"the undershoot should be half the width error ({LEFT_INSET / 2}px), got {-err:.1f}px")
