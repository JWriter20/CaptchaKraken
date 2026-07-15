"""
Shared runner for the CaptchaKraken Python demos.

The Python port is the *engine* (OpenCV detection + the vLLM planner). These demos
launch a real stealth browser (camoufox, using the binary from your fork —
JWriter20/camoufox releases; see README.md), navigate to a captcha demo site,
open the challenge, screenshot it, and run the engine on that frame. They report
token-generation speed, total time, and whether the engine produced a valid
solution — plus a best-effort reason when it didn't.

Note: full click-replay + multi-round verification in a live page is what the
TypeScript port (`captcha-kraken-js`) does end-to-end. Here we validate the
engine + model + server on a real challenge frame.
"""

import os
import tempfile
import time
from dataclasses import dataclass

from camoufox.sync_api import Camoufox

from captchakraken import CaptchaSolver
from captchakraken.action_types import ClickAction
from captchakraken.solver import UnsupportedCaptchaError


@dataclass
class DemoSpec:
    name: str
    url: str
    vendor: str  # "recaptcha" | "hcaptcha"


def _launch_kwargs() -> dict:
    kw = dict(headless=os.getenv("HEADLESS", "1") != "0", humanize=True, geoip=False)
    # Point camoufox at YOUR fork's binary. If unset, camoufox uses its default
    # cached binary (`python -m camoufox fetch`).
    binary = os.getenv("CAMOUFOX_BINARY") or os.getenv("CAMOUFOX_EXECUTABLE_PATH")
    if binary:
        kw["executable_path"] = binary
    return kw


def _capture_challenge(page, spec: DemoSpec) -> str:
    """Reach the challenge iframe and screenshot it to a temp PNG. Returns the path.

    Raises RuntimeError with a human message if the challenge never appears.
    """
    out = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name

    if spec.vendor == "recaptcha":
        anchor = page.wait_for_selector('iframe[src*="/recaptcha/api2/anchor"]', timeout=30_000)
        frame = anchor.content_frame()
        frame.click("#recaptcha-anchor", timeout=15_000)
        challenge = page.wait_for_selector('iframe[src*="/recaptcha/api2/bframe"]', timeout=30_000)
        # Let the tiles paint.
        page.wait_for_timeout(2500)
        challenge.screenshot(path=out)
        return out

    if spec.vendor == "hcaptcha":
        box = page.wait_for_selector('iframe[src*="hcaptcha.com"][src*="checkbox"], iframe[title*="checkbox"]',
                                     timeout=30_000)
        box.content_frame().click("#checkbox, div[role=checkbox]", timeout=15_000)
        challenge = page.wait_for_selector('iframe[title*="hCaptcha challenge"], iframe[src*="hcaptcha.com"][src*="frame=challenge"]',
                                           timeout=30_000)
        page.wait_for_timeout(2500)
        challenge.screenshot(path=out)
        return out

    raise RuntimeError(f"unknown vendor {spec.vendor!r}")


def _tokens_per_sec(solver: CaptchaSolver, elapsed_s: float) -> tuple[int, int, float]:
    """(input_tokens, output_tokens, tokens/sec) from the planner's usage log."""
    inp = out = 0
    for u in getattr(solver.planner, "token_usage", []) or []:
        inp += int(u.get("prompt_tokens", 0) or 0)
        out += int(u.get("completion_tokens", 0) or 0)
    tps = out / elapsed_s if out and elapsed_s > 0 else 0.0
    return inp, out, tps


def _explain(vendor: str, err: Exception) -> str:
    msg = str(err).lower()
    if isinstance(err, UnsupportedCaptchaError) or "cannot solve" in msg or "unsupported" in msg:
        if vendor == "hcaptcha":
            return ("hCaptcha served a non-grid challenge (drag / video / choose-the-card). "
                    "The engine handles image grids + checkboxes only — re-run to try for a grid.")
        return "Not a supported grid/checkbox challenge — re-run to try again."
    if "vllm" in msg or "connection" in msg or "refused" in msg or "max retries" in msg:
        return ("Could not reach the vLLM server — is it up and is VLLM_BASE_URL correct? "
                "(A local server auto-starts only if captchakraken[serve] is installed.)")
    if "timeout" in msg or "wait_for_selector" in msg:
        return "The challenge iframe never appeared (slow network, blocked widget, or the checkbox auto-passed)."
    return str(err) or "Unknown failure."


def run_demo(spec: DemoSpec) -> None:
    t0 = time.time()
    ok = False
    reason = None
    inp = out = 0
    tps = 0.0
    solve_s = 0.0
    plan_desc = "-"

    try:
        with Camoufox(**_launch_kwargs()) as browser:
            page = browser.new_page()
            page.goto(spec.url, wait_until="domcontentloaded", timeout=60_000)
            shot = _capture_challenge(page, spec)

            solver = CaptchaSolver()
            s0 = time.time()
            try:
                actions = solver.solve(shot, puzzle_source=spec.vendor)
            finally:
                solve_s = time.time() - s0
            inp, out, tps = _tokens_per_sec(solver, solve_s)

            acts = actions if isinstance(actions, list) else [actions]
            clicks = [a for a in acts if isinstance(a, ClickAction) and (a.target_bounding_boxes or [])]
            n_tiles = sum(len(a.target_bounding_boxes or []) for a in clicks)
            if n_tiles > 0:
                ok = True
                plan_desc = f"click plan: {n_tiles} tile(s)/target(s)"
            else:
                plan_desc = f"actions: {[type(a).__name__ for a in acts]}"
                reason = ("The model returned no tiles to click — either none matched the prompt, "
                          "or the challenge frame wasn't a clean grid. Re-run to try again.")
    except Exception as err:  # noqa: BLE001 — demo: report, don't traceback
        reason = _explain(spec.vendor, err)

    _report(spec, ok=ok, total_s=time.time() - t0, solve_s=solve_s,
            inp=inp, out=out, tps=tps, plan=plan_desc, reason=reason)


def _fmt(s: float) -> str:
    return f"{s:.1f}s" if s >= 1 else f"{int(s * 1000)}ms"


def _report(spec, *, ok, total_s, solve_s, inp, out, tps, plan, reason):
    line = "─" * 52
    print(f"\n{line}")
    print(f"  CaptchaKraken demo (engine) — {spec.name}")
    print(f"  {spec.url}")
    print(line)
    print(f"  result        : {'✓ engine produced a solution' if ok else '✗ no solution'}")
    print(f"  {plan}")
    print(f"  total time    : {_fmt(total_s)}   (solve: {_fmt(solve_s)})")
    print(f"  tokens        : {inp} in / {out} out")
    print(f"  gen speed     : {f'~{tps:.1f} tok/s' if tps > 0 else 'n/a'}")
    if reason:
        print(f"  reason        : {reason}")
    print(f"{line}\n")
    raise SystemExit(0 if ok else 1)
