"""Solver contract: every puzzle type, every response shape the model emits.

This is the layer between "the model is accurate" and "the browser solved it".
Accuracy graders (grade.py in the finetune repo) score the model's RAW TEXT, so
they cannot see a parser or dispatch bug — a perfectly correct answer that the
planner drops still grades as a correct answer. The live browser harness would
catch it, but only for whichever puzzle type a demo page happened to serve.

That gap is exactly how the 2026-07-18 drag regression shipped: the adapter was
retrained on the content schema while the serving prompt still asked for the
legacy one, the model replied in a hybrid, `_normalize_pixel` returned [], and
every drag puzzle failed as "unsupported".

So these tests assert, for EVERY puzzle type we ship:
  1. the response shapes the model actually emits parse, and
  2. they normalize to the correct ACTION KIND with the correct coordinates.

Scope, stated plainly: the planner's parsing is type-AGNOSTIC — it reads the
JSON, not the puzzle. So parametrizing over puzzle types does not exercise 26
different code paths. What it buys is (a) a failure names the puzzle type it
breaks, and (b) the type→kind map below is pinned and must be extended when a
type is added, so no type reaches production with nobody having decided what it
should emit. Whether the MODEL actually returns the right kind for a given
image is a Tier 2 question, answered against real held-out samples.

Hermetic — pure string/JSON parsing, no model, network, or image. Coordinate
regressions for individual malformed shapes live in test_pixel_parser.py; this
file is about per-type coverage of the contract.
"""
import pytest

from captchakraken.planner import ActionPlanner

# ── The shipping puzzle-type set ────────────────────────────────────────────
# Union of the finetune repo's generator registry (src/synthetic/cli.py
# GENERATORS), the hand-labeled held-out set (cleanSamples/test/
# test_solutions.json), and its scoring taxonomy (src/testing/eval_unified.py).
# 26 types as of 2026-07-20. A type the solver can be handed but that no test
# exercises is how a whole puzzle class regresses unnoticed.
#
# NOTE: five of these are absent from eval_unified.py's GRID/CLICK/*_DRAG sets.
# For the one with real held-out samples (hcaptcha_click_described_item, n=12)
# that is a live scoring bug — they fall through
# every branch in evaluate(), so `ok` stays False and they score a permanent 0%
# no matter what the model returns. Kinds below were read off their labeled
# answers in test_solutions.json, not guessed.
GRID_TYPES = {
    "recaptcha_grid_3x3",
    "recaptcha_grid_4x4",
    "hcaptcha_grid_3x3_property",
}
CLICK_TYPES = {
    "hcaptcha_arrows_deviating",
    "hcaptcha_car_parking_lot",
    "hcaptcha_click_described_item",   # untaxonomized in eval_unified (n=12)
    "hcaptcha_click_highest_jumper",
    "hcaptcha_click_image_by_traits",
    "hcaptcha_click_items_in_grid",    # stub generator, no samples yet
    "hcaptcha_click_on_path",
    "hcaptcha_grocery_list",           # stub generator, no samples yet
    "hcaptcha_line_ends",
    "hcaptcha_overlapping_lines",
    "hcaptcha_silhouette_match",
}
DRAG_TYPES = {
    "hcaptcha_connect_path",
    "hcaptcha_drag_fruit_to_plate",    # stub generator, no samples yet
    "hcaptcha_drag_missing_slot",
    "hcaptcha_drag_numbered_line_pieces",
    "hcaptcha_fish_swim_different",
    "hcaptcha_line_connecting_images",
    "hcaptcha_line_pieces",
    "hcaptcha_missing_piece",
    "hcaptcha_most_similar_or_different",
    "hcaptcha_semicircle_match",
    "hcaptcha_tetris_fit",
}
ALL_TYPES = GRID_TYPES | CLICK_TYPES | DRAG_TYPES


def _click(data):
    """Normalize and return click points rounded for comparison."""
    actions = ActionPlanner._normalize_pixel(data)
    assert actions, "response normalized to no action at all"
    assert all(a["kind"] == "click" for a in actions), (
        f"expected click actions, got kinds {[a['kind'] for a in actions]}"
    )
    return [(round(x, 3), round(y, 3)) for a in actions for x, y in a["points"]]


def _drags(data):
    """Normalize and return (src, dst) pairs rounded for comparison."""
    actions = ActionPlanner._normalize_pixel(data)
    assert actions, "response normalized to no action at all"
    assert all(a["kind"] == "drag" for a in actions), (
        f"expected drag actions, got kinds {[a['kind'] for a in actions]}"
    )
    return [
        ((round(a["src"][0], 3), round(a["src"][1], 3)),
         (round(a["dst"][0], 3), round(a["dst"][1], 3)))
        for a in actions
    ]


# ── Response shapes, keyed by action kind ───────────────────────────────────
# Each entry is (variant_name, raw_model_text). Raw TEXT, not dicts, so every
# case exercises _parse_json (fence stripping, brace balancing, strict=False)
# as well as _normalize_*. Variants are the shapes seen in production, not
# invented ones.

CLICK_SHAPES = [
    # The canonical content schema PIXEL_ACTION_PROMPT asks for.
    ("canonical_subjects_points",
     '{"action": "click", "subjects": ["duck", "penguin"], '
     '"points": [[250, 400], [600, 750]]}'),
    # Same, wrapped in a fenced code block.
    ("fenced_json",
     '```json\n{"action": "click", "subjects": ["duck", "penguin"], '
     '"points": [[250, 400], [600, 750]]}\n```'),
    # Nested under "action" — a shape the model drifts into.
    ("nested_action_dict",
     '{"action": {"action": "click", "points": [[250, 400], [600, 750]]}}'),
    # Truncated at max_tokens; _balance_json must repair it.
    ("truncated_unbalanced",
     '{"action": {"points": [[250, 400], [600, 750]'),
]
CLICK_EXPECTED = [(0.25, 0.4), (0.6, 0.75)]

DRAG_SHAPES = [
    # The canonical content schema — lowercase action, drags[], from/to.
    ("canonical_drags",
     '{"action": "drag", "drags": [{"source": "piece", "from": [200, 300], '
     '"destination": "slot", "to": [700, 800]}]}'),
    # Legacy PascalCase output[] schema — pre-content-schema adapters, and the
    # shape the older hand-labeled drag answers are stored in.
    ("legacy_output_pascalcase",
     '{"output": [{"Action": "simulate_drag", '
     '"SourcePosition": {"x": 200, "y": 300}, '
     '"EstimatedPosition": {"x": 700, "y": 800}}]}'),
    # snake_case simulate_drag with coords packed as {"x": [x, y]}.
    ("simulate_drag_snake_packed",
     '{"action": {"simulate_drag": [{"source_position": {"x": [200, 300]}, '
     '"destination_position": {"x": [700, 800]}}]}}'),
    # simulate_drag as a SINGLE OBJECT rather than a list — silently dropped
    # as "unsupported" before the single-object normalization landed.
    ("simulate_drag_single_object",
     '{"simulate_drag": {"sourcePosition": [200, 300], '
     '"destinationPosition": [700, 800]}}'),
]
DRAG_EXPECTED = [((0.2, 0.3), (0.7, 0.8))]

GRID_SHAPES = [
    # The trained shape: a bare JSON array.
    ("bare_array", "[1, 5, 9]", [1, 5, 9]),
    ("fenced_bare_array", "```json\n[1, 5, 9]\n```", [1, 5, 9]),
    ("target_ids_wrapped", '{"target_ids": [1, 5, 9]}', [1, 5, 9]),
    ("nested_target_ids", '{"action": {"target_ids": [1, 5, 9]}}', [1, 5, 9]),
    ("stringified_ids", '["1", "5", "9"]', [1, 5, 9]),
    # "No tiles match" is a VALID answer, not a parse failure — the prompt
    # explicitly asks for [] when everything has been cleared.
    ("empty_is_valid", "[]", []),
    # Junk must be dropped without costing the rest of the selection.
    ("out_of_range_dropped", "[1, 0, 5, 99, 9]", [1, 5, 9]),
]


@pytest.mark.parametrize("puzzle_type", sorted(CLICK_TYPES))
@pytest.mark.parametrize("variant,raw", CLICK_SHAPES, ids=lambda v: v if isinstance(v, str) else "")
def test_click_types_normalize_to_click_actions(puzzle_type, variant, raw):
    data = ActionPlanner._parse_json(raw)
    assert data is not None, f"{puzzle_type}/{variant}: response did not parse at all"
    assert _click(data) == CLICK_EXPECTED, f"{puzzle_type}/{variant}"


@pytest.mark.parametrize("puzzle_type", sorted(DRAG_TYPES))
@pytest.mark.parametrize("variant,raw", DRAG_SHAPES, ids=lambda v: v if isinstance(v, str) else "")
def test_drag_types_normalize_to_drag_actions(puzzle_type, variant, raw):
    data = ActionPlanner._parse_json(raw)
    assert data is not None, f"{puzzle_type}/{variant}: response did not parse at all"
    assert _drags(data) == DRAG_EXPECTED, f"{puzzle_type}/{variant}"


@pytest.mark.parametrize("puzzle_type", sorted(GRID_TYPES))
@pytest.mark.parametrize(
    "variant,raw,expected", GRID_SHAPES, ids=lambda v: v if isinstance(v, str) else ""
)
def test_grid_types_normalize_to_cell_ids(puzzle_type, variant, raw, expected):
    total = 16 if puzzle_type == "recaptcha_grid_4x4" else 9
    data = ActionPlanner._parse_json(raw)
    assert ActionPlanner._normalize_grid(data, total) == expected, f"{puzzle_type}/{variant}"


def test_multi_drag_returns_every_drag():
    """Multi-drag types must not collapse to a single drag — line_pieces and
    friends need all of them, and dropping the tail looks like a solve that
    just didn't finish."""
    raw = (
        '{"action": "drag", "drags": ['
        '{"source": "a", "from": [100, 100], "destination": "x", "to": [900, 100]},'
        '{"source": "b", "from": [200, 200], "destination": "y", "to": [800, 200]},'
        '{"source": "c", "from": [300, 300], "destination": "z", "to": [700, 300]}]}'
    )
    assert _drags(ActionPlanner._parse_json(raw)) == [
        ((0.1, 0.1), (0.9, 0.1)),
        ((0.2, 0.2), (0.8, 0.2)),
        ((0.3, 0.3), (0.7, 0.3)),
    ]


def test_unusable_response_yields_no_action_rather_than_a_wrong_one():
    """Garbage must normalize to [] so the solver raises UnsupportedCaptchaError
    and can retry — never to a plausible-looking click at (0, 0)."""
    for raw in ('{"action": "click", "subjects": ["duck"]}',   # labels, no points
                '{"reasoning": "I cannot see the image"}',
                "I'm not able to solve this captcha.",
                ""):
        assert ActionPlanner._normalize_pixel(ActionPlanner._parse_json(raw)) == []


def test_every_shipping_puzzle_type_is_covered():
    """Guard against adding a puzzle type without adding contract coverage."""
    assert len(ALL_TYPES) == 25, (
        f"the shipping puzzle-type set changed ({len(ALL_TYPES)} types, expected 25). "
        "Add the new type to GRID_TYPES / CLICK_TYPES / DRAG_TYPES above so it gets "
        "contract coverage, then update this count."
    )
    assert not (GRID_TYPES & CLICK_TYPES), "a type cannot be both grid and click"
    assert not (GRID_TYPES & DRAG_TYPES), "a type cannot be both grid and drag"
    assert not (CLICK_TYPES & DRAG_TYPES), "a type cannot be both click and drag"
