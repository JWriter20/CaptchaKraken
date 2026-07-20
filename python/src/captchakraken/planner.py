"""
ActionPlanner (v2) — talks to local vLLM serving the `captcha` LoRA over the
OpenAI-compatible /v1/chat/completions endpoint.

v1 supported transformers/vllm-local/gemini/openrouter and a whole tool-using
planner with detect/segment/drag-refine; that code is preserved on the
`v1-old-architecture` branch.
"""

import base64
import json
import os
import re
import sys
from mimetypes import guess_type
from typing import Any, Dict, List, Optional

import requests

from . import config
from .server_manager import ensure_server
from .timing import timed

DEBUG = os.getenv("CAPTCHA_DEBUG", "0") == "1"


# Matches the training-distribution grid prompts in
# cleanSamples/test/test_solutions.json -> grade.synthesize_instruction.
# Drifting from these costs measurable accuracy.
SELECT_GRID_PROMPT = """Solve the captcha grid by choosing the cell numbers that match the description from the captcha image prompt.

Grid: {rows}x{cols} ({total} cells)
{grid_hint}

If no tiles match the description (e.g., they have all been cleared or none were present), return an empty list for target_ids: [].

Return JSON Array: [list of cell numbers (1-{total})]"""


# Non-grid click/drag puzzles. MUST stay byte-identical to
# src/synthetic/reasoning/instructions.py::ACTION_INSTRUCTION in the finetune
# repo — that is the exact prompt the LoRA was trained (and graded) on. Drift
# here silently degrades every click/drag puzzle. Coordinates come back on a
# 0–1000 scale; the solver converts them to 0–1 bboxes.
PIXEL_ACTION_PROMPT = (
    "Your task is to solve the captcha. Read the instruction at the top of the image carefully.\n\n"
    "Look at the puzzle and decide what action solves it. All coordinates you return must be on a "
    "normalized 0–1000 image scale (top-left = (0, 0), bottom-right = (1000, 1000)).\n\n"
    "Choose ONE response:\n\n"
    "FOR CLICK PUZZLES:\n"
    "  Identify every position you need to click and emit them as a list of points:\n"
    "  → \"action\": { \"action\": \"click\", \"points\": [[x1, y1], [x2, y2], ...] }\n\n"
    "FOR DRAG PUZZLES:\n"
    "  Drag ONE item at a time. The source position is the centroid of the piece you are picking up; "
    "the destination position is where it should end up. If multiple drags are needed, drag the topmost "
    "item first.\n"
    "  → \"output\": [{ \"Action\": \"simulate_drag\", "
    "\"SourceDescription\": \"...\", \"SourcePosition\": { \"x\": 1-1000, \"y\": 1-1000 }, "
    "\"DestinationDescription\": \"...\", \"EstimatedPosition\": { \"x\": 1-1000, \"y\": 1-1000 } }]\n\n"
    "Respond ONLY with JSON:\n"
    "{\n"
    "  \"action\": { ... }\n"
    "  // OR \"output\": [ ... ]\n"
    "}"
)


class ActionPlanner:
    """Thin client for the vLLM `captcha` LoRA."""

    def __init__(
        self,
        model: Optional[str] = None,
        debug_callback: Optional[Any] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        **_: Any,
    ):
        self.debug_callback = debug_callback
        self.token_usage: List[Dict[str, Any]] = []

        # All model/endpoint defaults come from `config` (env-overridable), so
        # the planner itself is model-agnostic — swap models via env, not code.
        self.model = model or config.lora_name()
        self.base_url = base_url or config.base_url()
        self.api_key = api_key or config.api_key()
        # Auto-start a local vLLM server on the first request if one isn't up
        # (no-op for a healthy or remote endpoint). Guarded so we only try once.
        self._server_ensured = False

    def _log(self, message: str) -> None:
        if DEBUG:
            print(f"[Planner] {message}", file=sys.stderr)
        if self.debug_callback:
            self.debug_callback(f"[Planner] {message}")

    def _chat_with_image(self, prompt: str, image_path: str, max_tokens: int = 512) -> str:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        mime, _ = guess_type(image_path)
        if mime is None:
            mime = "image/png"

        messages = [
            {
                "role": "system",
                "content": "You are an expert captcha solver. Respond ONLY with the JSON action.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            },
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            # Qwen3.5's reasoning otherwise eats the token budget. `/no_think`
            # in the prompt alone is unreliable; disabling at the chat-template
            # level is the documented way.
            "chat_template_kwargs": {"enable_thinking": False},
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Hands-off server: before the very first request, make sure something
        # is listening (auto-start a local vLLM if needed; no-op if it's already
        # up or the endpoint is remote). Best-effort — a failure here surfaces as
        # a normal connection error on the request below.
        if not self._server_ensured:
            try:
                ensure_server(self.base_url)
            except Exception as e:  # noqa: BLE001 — don't mask the real request error
                self._log(f"ensure_server: {e}")
            self._server_ensured = True

        url = f"{self.base_url}/chat/completions"
        self._log(f"POST {url} model={self.model} max_tokens={max_tokens}")

        with timed("planner.chat"):
            resp = requests.post(url, headers=headers, json=payload, timeout=120)

        # Surface auth / server errors with the actual body instead of letting
        # resp.json() blow up with a cryptic "Expecting value: line 1 column 1"
        # on a non-JSON response (e.g. a 401 {"error":"Unauthorized"} or an
        # HTML error page). 401/403 almost always means the bearer token
        # (CAPTCHA_KRAKEN_API_KEY) didn't reach this process.
        if not resp.ok:
            body = (resp.text or "")[:300]
            hint = ""
            if resp.status_code in (401, 403):
                hint = (
                    " — check CAPTCHA_KRAKEN_API_KEY is set and forwarded to the CLI"
                )
            raise RuntimeError(
                f"vLLM {resp.status_code} {resp.reason} at {url}{hint}. Body: {body}"
            )

        try:
            data = resp.json()
        except ValueError:
            body = (resp.text or "")[:300]
            raise RuntimeError(
                f"vLLM returned a non-JSON body from {url} (is the server up and "
                f"is VLLM_BASE_URL correct?). Body: {body}"
            )

        if data.get("usage"):
            self.token_usage.append(data["usage"])

        content = data["choices"][0]["message"].get("content") or ""
        self._log(f"Raw content: {content[:300]}")
        return content

    @staticmethod
    def _parse_json(text: str) -> Any:
        text = (text or "").strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0]

        start_obj = text.find("{")
        start_list = text.find("[")
        if start_list != -1 and (start_obj == -1 or start_list < start_obj):
            start = start_list
            end = text.rfind("]") + 1
        elif start_obj != -1:
            start = start_obj
            end = text.rfind("}") + 1
        else:
            return None

        # strict=False tolerates unescaped control chars INSIDE strings — the
        # model routinely emits coordinates as pretty-printed strings with a
        # literal newline, e.g. "click": ["277,\n  728", ...], which strict JSON
        # rejects. Without this the whole response fails to parse and a solvable
        # click puzzle is dropped as "unsupported".
        try:
            return json.loads(text[start:end], strict=False)
        except json.JSONDecodeError:
            # The model sometimes truncates (hit max_tokens) or over-nests its
            # pretty-printed JSON, leaving brackets unclosed. Repair by balancing
            # from the first opener to the end of the raw text.
            repaired = ActionPlanner._balance_json(text[start:])
            if repaired is not None:
                try:
                    return json.loads(repaired, strict=False)
                except json.JSONDecodeError:
                    return None
            return None

    @staticmethod
    def _balance_json(text: str) -> Optional[str]:
        """Close any brackets/braces the model left open (string-aware), so a
        truncated ``{"action": {"points": [[1,2],`` still parses. Returns None if
        there was nothing to balance."""
        stack: List[str] = []
        in_str = False
        esc = False
        for ch in text:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append(ch)
            elif ch == "}":
                if stack and stack[-1] == "{":
                    stack.pop()
            elif ch == "]":
                if stack and stack[-1] == "[":
                    stack.pop()
        if not stack and not in_str:
            return None
        out = text
        if in_str:
            out += '"'
        # Drop a dangling comma / partial token before the closers.
        out = out.rstrip().rstrip(",")
        for opener in reversed(stack):
            out += "}" if opener == "{" else "]"
        return out

    def get_grid_selection(
        self,
        image_path: str,
        rows: int,
        cols: int,
        retry_mode: Optional[str] = None,
    ) -> List[int]:
        """Return the list of 1-indexed cells the model wants to click.

        retry_mode == "missed-tiles": the previous submission was rejected
        by the captcha vendor with an under-selection error. Append an
        explicit recovery instruction that tells the model the FULL grid
        contains at least one matching tile it didn't pick last time. This
        nudges it off the "I already covered everything" attractor.
        """
        total = rows * cols
        if rows == 4 and cols == 4:
            grid_hint = "Hint: Single large image split into tiles. Select ALL parts."
        else:
            grid_hint = "Hint: Separate images. Select only clear matches."

        prompt = SELECT_GRID_PROMPT.format(
            rows=rows, cols=cols, total=total, grid_hint=grid_hint
        )
        if retry_mode == "missed-tiles":
            prompt = (
                prompt
                + "\n\nIMPORTANT: A previous submission was rejected because not all "
                  "matching tiles were selected. Re-examine EVERY cell in the grid "
                  "carefully. There is at least one more matching tile you missed. "
                  "Return the complete list of cell numbers that match the description, "
                  "including any matches you may have overlooked."
            )
        raw = self._chat_with_image(prompt, image_path, max_tokens=128)
        data = self._parse_json(raw)

        if isinstance(data, list):
            ids = data
        elif isinstance(data, dict):
            ids = data.get("target_ids") or data.get("action", {}).get("target_ids") or []
        else:
            ids = []

        out: List[int] = []
        for v in ids:
            try:
                iv = int(v)
                if 1 <= iv <= total:
                    out.append(iv)
            except (TypeError, ValueError):
                continue
        self._log(f"grid selection -> {out}")
        return out

    def get_pixel_actions(self, image_path: str) -> List[Dict[str, Any]]:
        """Solve a non-grid click/drag puzzle.

        Sends the trained action prompt + image, parses the model's JSON, and
        returns a list of normalized actions with all coordinates on a 0–1
        scale:

          {"kind": "click", "points": [(x, y), ...]}
          {"kind": "drag",  "src": (x, y), "dst": (x, y)}

        Returns [] if the model produced nothing usable. The solver turns these
        into ClickAction / DragAction bboxes.
        """
        raw = self._chat_with_image(PIXEL_ACTION_PROMPT, image_path, max_tokens=512)
        data = self._parse_json(raw)
        actions = self._normalize_pixel(data)
        self._log(f"pixel actions -> {actions}")
        return actions

    @staticmethod
    def _normalize_pixel(data: Any) -> List[Dict[str, Any]]:
        """Map the model's 0–1000 click/drag JSON to 0–1 normalized actions.

        Tolerant of the two trained shapes plus a few near-misses:
          click: {"action": {"action": "click", "points": [[x, y], ...]}}
                 or top-level {"points": [...]} / {"action": {"points": [...]}}
          drag:  {"output": [{"Action": "simulate_drag",
                              "SourcePosition": {x, y},
                              "EstimatedPosition": {x, y}}, ...]}
        """
        def norm_xy(x: Any, y: Any) -> Optional[tuple]:
            try:
                fx, fy = float(x) / 1000.0, float(y) / 1000.0
            except (TypeError, ValueError):
                return None
            if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
                # Some outputs use 0–1 already; accept those too.
                if 0.0 <= float(x) <= 1.0 and 0.0 <= float(y) <= 1.0:
                    fx, fy = float(x), float(y)
                else:
                    fx, fy = min(max(fx, 0.0), 1.0), min(max(fy, 0.0), 1.0)
            return (fx, fy)

        def flat_numbers(v: Any) -> List[float]:
            """Every number anywhere inside v, in order. Handles nested lists,
            {x,y} dicts, bare numbers, and numeric strings like "277, 728" (the
            model sometimes emits coordinates as strings, occasionally even split
            across separate array elements)."""
            nums: List[float] = []
            if isinstance(v, bool):
                return nums
            if isinstance(v, (list, tuple)):
                for e in v:
                    nums.extend(flat_numbers(e))
            elif isinstance(v, dict):
                for k in ("x", "y", "X", "Y"):
                    if k in v:
                        nums.extend(flat_numbers(v[k]))
            elif isinstance(v, (int, float)):
                nums.append(float(v))
            elif isinstance(v, str):
                nums.extend(float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", v))
            return nums

        def coordish(v: Any) -> bool:
            """True when v is a non-empty list of COORDINATES (numbers, [x,y]
            pairs, {x,y} dicts, or numeric strings) — not text labels like
            "dog". Used to decide whether a "click"/"coordinates" array carries
            points we should salvage."""
            if not isinstance(v, (list, tuple)) or not v:
                return False
            for e in v:
                if isinstance(e, bool):
                    return False
                if isinstance(e, (int, float, list, tuple, dict)):
                    continue
                if isinstance(e, str) and re.search(r"\d", e):
                    continue
                return False
            return True

        out: List[Dict[str, Any]] = []
        if not isinstance(data, dict):
            return out

        # ---- drag: {"output": [ {simulate_drag ...}, ... ]} ----
        drags = data.get("output")
        if isinstance(drags, list) and drags:
            for d in drags:
                if not isinstance(d, dict):
                    continue
                sp = d.get("SourcePosition") or {}
                ep = d.get("EstimatedPosition") or d.get("DestinationPosition") or {}
                src = norm_xy(sp.get("x"), sp.get("y")) if isinstance(sp, dict) else None
                dst = norm_xy(ep.get("x"), ep.get("y")) if isinstance(ep, dict) else None
                if src and dst:
                    out.append({"kind": "drag", "src": src, "dst": dst})
            if out:
                return out

        # ---- drag: {"action": {"simulate_drag": [{source_position,
        #                                           destination_position}]}} ----
        # The full-puzzle LoRA actually emits drags in this snake_case shape
        # (not the prompt's {"output":[{"Action":"simulate_drag",...}]}), and
        # packs each coordinate as {"x": [x, y]} — flat_numbers() pulls the pair
        # out of that. Without this branch a correctly-solved drag (e.g. hCaptcha
        # "drag ONE character to the matching character") is dropped as
        # "unsupported".
        sd = data.get("simulate_drag")
        if sd is None and isinstance(action := data.get("action"), dict):
            sd = action.get("simulate_drag")
        # The model emits simulate_drag as EITHER a list of drags OR a single
        # drag object; normalize the single-object form to a one-element list so
        # both parse (the object form was silently dropped as "unsupported").
        if isinstance(sd, dict):
            sd = [sd]
        if isinstance(sd, list) and sd:
            def _drag_coords(obj: Any, *roles: str) -> List[float]:
                # Pull the source/destination coordinate pair regardless of key
                # casing or separators — the LoRA freely varies between
                # source_position / sourcePosition / SourcePosition and
                # destination_position / destinationPosition / EstimatedPosition.
                # The coord value may be {"x": [x, y]}, [x, y], {x, y}, or a
                # string; flat_numbers() handles all of those.
                if not isinstance(obj, dict):
                    return []
                for k, v in obj.items():
                    kn = str(k).lower().replace("_", "").replace("-", "")
                    if "pos" not in kn:
                        continue
                    if any(r in kn for r in roles):
                        nums = flat_numbers(v)
                        if len(nums) >= 2:
                            return nums
                return []
            for d in sd:
                if not isinstance(d, dict):
                    continue
                snums = _drag_coords(d, "source", "src")
                dnums = _drag_coords(d, "destination", "dest", "estimated", "target")
                if len(snums) >= 2 and len(dnums) >= 2:
                    src = norm_xy(snums[0], snums[1])
                    dst = norm_xy(dnums[0], dnums[1])
                    if src and dst:
                        out.append({"kind": "drag", "src": src, "dst": dst})
            if out:
                return out

        # ---- click: {"action": {"action":"click","points":[...]}} ----
        action = data.get("action")
        # The model sometimes over-wraps: {"action": {"action": {"action":
        # "click", "points":[...]}}}. Peel nested "action" dicts until we reach
        # the one carrying points/coords (bounded so we never loop forever).
        for _ in range(4):
            if (
                isinstance(action, dict)
                and action.get("points") is None
                and action.get("source") is None
                and isinstance(action.get("action"), dict)
            ):
                action = action["action"]
            else:
                break
        points = None
        if isinstance(action, dict):
            points = action.get("points")
            # single drag emitted under "action"
            if points is None and action.get("action") == "drag":
                src = norm_xy(*(action.get("source") or (None, None)))
                dst = norm_xy(*(action.get("target") or (None, None)))
                if src and dst:
                    return [{"kind": "drag", "src": src, "dst": dst}]
        if points is None:
            points = data.get("points")
        # Salvage: some responses carry the click coordinates under "click" (or
        # "coordinates") instead of "points", as [x,y] pairs, {x,y} dicts, or
        # "x, y" strings (sometimes split across elements), with no "points" key
        # at all. Pull every number out of a coordinate-like value and pair them
        # into points so a well-intentioned answer isn't dropped as "unsupported".
        # Skipped when the value is text labels ("dog", "duck") rather than coords.
        if not (isinstance(points, list) and points):
            for container in (action if isinstance(action, dict) else None, data):
                if not isinstance(container, dict):
                    continue
                cand = container.get("click")
                if cand is None:
                    cand = container.get("coordinates")
                if coordish(cand):
                    nums = flat_numbers(cand)
                    if len(nums) >= 2:
                        points = [
                            [nums[i], nums[i + 1]] for i in range(0, len(nums) - 1, 2)
                        ]
                        break
        if isinstance(points, list) and points:
            pts = []
            for p in points:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    xy = norm_xy(p[0], p[1])
                elif isinstance(p, dict):
                    xy = norm_xy(p.get("x"), p.get("y"))
                else:
                    xy = None
                if xy:
                    pts.append(xy)
            if pts:
                out.append({"kind": "click", "points": pts})
        return out
