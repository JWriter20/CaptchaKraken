"""
CaptchaKraken — OpenCV grid detection + a fine-tuned Qwen3.5-9B vision LoRA
served on vLLM.

Usage:
    from captchakraken import CaptchaSolver
    solver = CaptchaSolver()   # auto-starts / connects to a local vLLM server
    actions = solver.solve("captcha.png")

Model/endpoint defaults live in `captchakraken.config` and are fully
env-overridable (VLLM_BASE_URL, CAPTCHA_LORA_ADAPTER, …); the solver itself is
model-agnostic. The legacy v1 stack (SAM3 grounding, multi-provider planner,
detect/segment/drag-refine) lives on the `v1-old-architecture` branch.
"""

from pathlib import Path

try:  # pragma: no cover
    from dotenv import load_dotenv

    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")
except Exception:
    pass

from .action_types import (
    CaptchaAction,
    ClickAction,
    DragAction,
    TypeAction,
    WaitAction,
)
from .image_processor import ImageProcessor
from .overlay import add_overlays_to_image

# The planner (requests) and solver (torch/vllm/transformers) pull in the heavy
# serving stack. Keep them optional so leaf modules — e.g. tool_calls.find_grid,
# which needs only cv2 + numpy + pillow — can be imported in a minimal env (CI's
# hermetic grid-detection test) without the full GPU dependency set installed.
try:  # pragma: no cover - exercised only when the serving stack is installed
    from .planner import ActionPlanner
    from .solver import CaptchaSolver, solve_captcha

    # The page driver sits behind the same guard: it imports `solver`, so it has
    # the same dependency floor. It needs NO browser package of its own — the
    # caller supplies the Playwright-compatible page (see page_solver's module
    # docstring on why we duck-type rather than import one).
    from .page_solver import PageSolver, SolveResult, solve_captcha_on_page
except ModuleNotFoundError:
    ActionPlanner = None  # type: ignore[assignment,misc]
    CaptchaSolver = None  # type: ignore[assignment,misc]
    solve_captcha = None  # type: ignore[assignment]
    PageSolver = None  # type: ignore[assignment,misc]
    SolveResult = None  # type: ignore[assignment,misc]
    solve_captcha_on_page = None  # type: ignore[assignment]

__all__ = [
    "CaptchaSolver",
    "solve_captcha",
    "PageSolver",
    "SolveResult",
    "solve_captcha_on_page",
    "ActionPlanner",
    "ImageProcessor",
    "CaptchaAction",
    "ClickAction",
    "DragAction",
    "TypeAction",
    "WaitAction",
    "add_overlays_to_image",
]

__version__ = "2.3.0"
