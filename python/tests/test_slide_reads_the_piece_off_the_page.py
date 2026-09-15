import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from captchakraken.page_solver import PageSolver, PageSolverConfig

BOX = {"x": 0.0, "y": 0.0, "width": 340.0, "height": 400.0}
TARGET = 245.0
TRUE_PIECE_W = 80.0
DIFF_PIECE_W = 63.0
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
        orig_left = TRUE_PIECE_W / 2 - TRUE_PIECE_W / 2
        return {"bbox": [orig_left + LEFT_INSET, 0.0,
                         state["piece_centre"]() + TRUE_PIECE_W / 2, TRUE_PIECE_W],
                "piece": None}

    class _NullHuman:
        def press(self, page): pass
        def release(self, page): pass
        def pause(self, kind): pass

    def measure_piece_box(scope, widget_width):
        if not piece_in_dom:
            return None
        return {"x": state["piece_centre"]() - TRUE_PIECE_W / 2, "y": 100.0,
                "width": TRUE_PIECE_W, "height": TRUE_PIECE_W}

    s._measure_piece_box = measure_piece_box
    s._find_control = find_control
    s._smooth_move = smooth_move
    s._track_piece = track_piece
    s._shot_scale = lambda shot, css_w: 1.0
    s._move = lambda *a, **k: None
    s._human = _NullHuman()
    s._screenshot = lambda *a, **k: None

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
    err = _run(piece_in_dom=False) - TARGET
    assert err < -4.0, f"expected the known undershoot, got {err:.1f}px"
    assert abs(err + LEFT_INSET / 2) < 1.5, (
        f"the undershoot should be half the width error ({LEFT_INSET / 2}px), got {-err:.1f}px")
