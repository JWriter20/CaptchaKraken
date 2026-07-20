"""
Parser-robustness regression tests for the full-puzzle (click/drag) path.

The v1.1 model solves click puzzles ("click the duck, penguin, mouse") by
returning pixel coordinates — but it emits them in shapes that the old parser
silently dropped, turning a perfectly solvable click puzzle into a false
"unsupported":

  * coordinates as pretty-printed strings with a literal newline inside them,
    e.g. `"click": ["277,\\n  728", ...]` — which strict `json.loads` rejects, so
    the ENTIRE response failed to parse.
  * coordinates delivered under a `"click"` key (as "x, y" strings, sometimes
    split across elements) instead of the expected `"points": [[x, y], ...]`.

These lock in the two fixes (strict=False parsing + coordinate salvage). Hermetic
— pure string/JSON parsing, no model or network.
"""
from captchakraken.planner import ActionPlanner


def _click_points(data):
    r = ActionPlanner._normalize_pixel(data)
    pts = r[0]["points"] if r and r[0].get("kind") == "click" else []
    return [(round(x, 3), round(y, 3)) for x, y in pts]


def test_parse_json_tolerates_unescaped_newlines_in_strings():
    # Exactly what the model emits: coordinate strings with literal newlines.
    raw = '{"action": {"click": ["277,\n  728", "429,\n  477"]}}'
    data = ActionPlanner._parse_json(raw)
    assert isinstance(data, dict)
    assert _click_points(data) == [(0.277, 0.728), (0.429, 0.477)]


def test_salvage_coordinates_under_click_key():
    data = {"action": {"click": ["277, 728", "429, 477", "715, 611"]}}
    assert _click_points(data) == [(0.277, 0.728), (0.429, 0.477), (0.715, 0.611)]


def test_salvage_split_coordinate_strings():
    # x and y split across separate array elements.
    data = {"action": {"click": ["277,", "728", "429,", "477"]}}
    assert _click_points(data) == [(0.277, 0.728), (0.429, 0.477)]


def test_salvage_coordinates_key():
    data = {"action": {"coordinates": [[277, 728]]}}
    assert _click_points(data) == [(0.277, 0.728)]


def test_labels_only_yields_no_false_points():
    # A "click" array of TEXT labels must NOT be misread as coordinates.
    assert _click_points({"action": {"click": ["dog", "duck", "mouse"]}}) == []


def test_points_key_wins_over_click_labels():
    data = {"action": {"click": ["dog", "duck"], "points": [[506, 479], [788, 609]]}}
    assert _click_points(data) == [(0.506, 0.479), (0.788, 0.609)]


def test_proper_points_still_parse():
    data = {"action": {"action": "click", "points": [[100, 200], [300, 400]]}}
    assert _click_points(data) == [(0.1, 0.2), (0.3, 0.4)]


def test_end_to_end_real_response_shape():
    # The verbatim shape captured from the live model on a failing frame.
    raw = (
        '{\n  "action": {\n    "click": [\n'
        '      "277,\n      728",\n'
        '      "429,\n      477",\n'
        '      "715,\n      611"\n'
        "    ]\n  }\n}"
    )
    data = ActionPlanner._parse_json(raw)
    assert _click_points(data) == [(0.277, 0.728), (0.429, 0.477), (0.715, 0.611)]
