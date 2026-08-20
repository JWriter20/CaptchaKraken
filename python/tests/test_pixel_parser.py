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


# ── the two families the parser used to drop on the floor ───────────────────
# Both are shapes the generation-2 prompt EXPLICITLY asks for, so a model that
# answers them perfectly still solved nothing: the normalizer returned [], the
# solver raised "unsupported", and the driver never acted.

def test_a_typed_answer_survives_normalization():
    """`{"action": "type", "text": "..."}` — what TEXT_INSTRUCTION asks for on
    botdetect/mtcaptcha/yandex. There is no coordinate anywhere in this answer,
    and every branch of the old normalizer keyed off one."""
    assert ActionPlanner._normalize_pixel(
        {"action": "type", "text": "aB3dK"}
    ) == [{"kind": "type", "text": "aB3dK"}]


def test_a_typed_answer_keeps_case_and_spacing_verbatim():
    """The code IS the answer — normalizing it (stripping, upper-casing, ...)
    would silently submit something the model did not read off the image."""
    out = ActionPlanner._normalize_pixel({"action": "type", "text": " 7hE q "})
    assert out == [{"kind": "type", "text": " 7hE q "}]


def test_an_empty_typed_answer_is_not_an_action():
    assert ActionPlanner._normalize_pixel({"action": "type", "text": ""}) == []


def test_sourceless_drag_is_a_slide_not_a_dropped_action():
    """The PUZZLE PIECE SLIDER clause tells the model to leave the source EMPTY
    and give only the destination. The old drag branch required BOTH ends
    (`len(snums) >= 2 and len(dnums) >= 2`), so the one answer shape the prompt
    asks for on every slide puzzle was the one shape that parsed to nothing."""
    assert ActionPlanner._normalize_pixel({
        "action": "drag",
        "drags": [{"source": "", "from": [], "destination": "slot", "to": [612, 344]}],
    }) == [{"kind": "slide", "dst": (0.612, 0.344)}]


def test_sourceless_drag_with_the_from_key_absent_entirely():
    assert ActionPlanner._normalize_pixel({
        "action": "drag", "drags": [{"destination": "slot", "to": [500, 500]}],
    }) == [{"kind": "slide", "dst": (0.5, 0.5)}]


def test_a_two_ended_drag_is_still_a_drag():
    """The slide branch must not swallow ordinary drags — hCaptcha's puzzles
    depend on the source being picked up."""
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
    """A source with nowhere to go is not actionable — the driver would pick the
    piece up and have no target to release it on."""
    assert ActionPlanner._normalize_pixel({
        "action": "drag", "drags": [{"source": "piece", "from": [100, 200]}],
    }) == []
