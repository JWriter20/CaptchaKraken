from captchakraken.planner import ActionPlanner


def _click_points(data):
    r = ActionPlanner._normalize_pixel(data)
    pts = r[0]["points"] if r and r[0].get("kind") == "click" else []
    return [(round(x, 3), round(y, 3)) for x, y in pts]


def test_parse_json_tolerates_unescaped_newlines_in_strings():
    raw = '{"action": {"click": ["277,\n  728", "429,\n  477"]}}'
    data = ActionPlanner._parse_json(raw)
    assert isinstance(data, dict)
    assert _click_points(data) == [(0.277, 0.728), (0.429, 0.477)]


def test_salvage_coordinates_under_click_key():
    data = {"action": {"click": ["277, 728", "429, 477", "715, 611"]}}
    assert _click_points(data) == [(0.277, 0.728), (0.429, 0.477), (0.715, 0.611)]


def test_salvage_split_coordinate_strings():
    data = {"action": {"click": ["277,", "728", "429,", "477"]}}
    assert _click_points(data) == [(0.277, 0.728), (0.429, 0.477)]


def test_salvage_coordinates_key():
    data = {"action": {"coordinates": [[277, 728]]}}
    assert _click_points(data) == [(0.277, 0.728)]


def test_labels_only_yields_no_false_points():
    assert _click_points({"action": {"click": ["dog", "duck", "mouse"]}}) == []


def test_points_key_wins_over_click_labels():
    data = {"action": {"click": ["dog", "duck"], "points": [[506, 479], [788, 609]]}}
    assert _click_points(data) == [(0.506, 0.479), (0.788, 0.609)]


def test_proper_points_still_parse():
    data = {"action": {"action": "click", "points": [[100, 200], [300, 400]]}}
    assert _click_points(data) == [(0.1, 0.2), (0.3, 0.4)]


def test_end_to_end_real_response_shape():
    raw = (
        '{\n  "action": {\n    "click": [\n'
        '      "277,\n      728",\n'
        '      "429,\n      477",\n'
        '      "715,\n      611"\n'
        "    ]\n  }\n}"
    )
    data = ActionPlanner._parse_json(raw)
    assert _click_points(data) == [(0.277, 0.728), (0.429, 0.477), (0.715, 0.611)]


def test_a_typed_answer_survives_normalization():
    assert ActionPlanner._normalize_pixel(
        {"action": "type", "text": "aB3dK"}
    ) == [{"kind": "type", "text": "aB3dK"}]


def test_a_typed_answer_keeps_case_and_spacing_verbatim():
    out = ActionPlanner._normalize_pixel({"action": "type", "text": " 7hE q "})
    assert out == [{"kind": "type", "text": " 7hE q "}]


def test_an_empty_typed_answer_is_not_an_action():
    assert ActionPlanner._normalize_pixel({"action": "type", "text": ""}) == []


def test_sourceless_drag_is_a_slide_not_a_dropped_action():
    assert ActionPlanner._normalize_pixel({
        "action": "drag",
        "drags": [{"source": "", "from": [], "destination": "slot", "to": [612, 344]}],
    }) == [{"kind": "slide", "dst": (0.612, 0.344)}]


def test_sourceless_drag_with_the_from_key_absent_entirely():
    assert ActionPlanner._normalize_pixel({
        "action": "drag", "drags": [{"destination": "slot", "to": [500, 500]}],
    }) == [{"kind": "slide", "dst": (0.5, 0.5)}]


def test_a_two_ended_drag_is_still_a_drag():
    assert ActionPlanner._normalize_pixel({
        "action": "drag",
        "drags": [{"source": "piece", "from": [100, 200],
                   "destination": "slot", "to": [700, 200]}],
    }) == [{"kind": "drag", "src": (0.1, 0.2), "dst": (0.7, 0.2)}]


def test_a_slide_and_a_drag_in_one_answer_keep_their_own_kinds():
    out = ActionPlanner._normalize_pixel({
        "action": "drag",
        "drags": [{"from": [], "to": [612, 344]},
                  {"from": [100, 200], "to": [700, 200]}],
    })
    assert [a["kind"] for a in out] == ["slide", "drag"]


def test_a_drag_with_no_destination_is_still_dropped():
    assert ActionPlanner._normalize_pixel({
        "action": "drag", "drags": [{"source": "piece", "from": [100, 200]}],
    }) == []
