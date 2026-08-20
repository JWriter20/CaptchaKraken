"""
Shared runner for the CaptchaKraken Python demos.

Launches a real stealth browser (camoufox, using the binary from your fork —
JWriter20/camoufox releases; see README.md), navigates to a page with a captcha
on it, and solves it THE WAY PRODUCTION DOES: `PageSolver` finds the widget,
opens the challenge, clicks, submits, and decides whether the vendor accepted.
It reports token-generation speed, total time, and whether the solve succeeded,
plus a best-effort reason when it didn't.

Point it at any URL:

    python examples/demoHcaptcha.py                       # the built-in demo page
    python examples/demoHcaptcha.py https://your.site/    # anything else
    python examples/demoHcaptcha.py https://your.site/ --headed

This used to be image-in / actions-out — screenshot the challenge, ask the
engine for a click plan, print how many tiles it would have clicked, and stop.
That measured the model but never the driver, and a recording of it showed a
captcha nobody touched. `captchakraken.page_solver` is the Python mirror of the
TypeScript driver, so both ports now demo the same end-to-end path, which is
what CLAUDE.md rule 1c asks for.
"""

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, replace

from camoufox.sync_api import Camoufox

from captchakraken.page_solver import CaptchaSolveError, PageSolver


@dataclass
class DemoSpec:
    name: str
    url: str
    vendor: str  # "recaptcha" | "hcaptcha" — advisory; PageSolver auto-detects


def spec_from_argv(default: DemoSpec, argv=None) -> DemoSpec:
    """Let any demo point at an arbitrary page.

    The vendor stays a default rather than being inferred from the URL: it only
    selects the wording of a failure explanation, and PageSolver detects the
    actual widget itself. Guessing it from a hostname would be wrong exactly on
    the pages worth demoing — your own site, embedding someone else's captcha.
    """
    ap = argparse.ArgumentParser(
        description=f"CaptchaKraken demo — {default.name}",
        epilog="With no URL, runs against the built-in demo page.")
    ap.add_argument("url", nargs="?", default=default.url,
                    help=f"page to solve (default: {default.url})")
    ap.add_argument("--vendor", default=default.vendor,
                    choices=("hcaptcha", "recaptcha"),
                    help="only affects failure wording; the solver auto-detects")
    ap.add_argument("--name", default=None, help="label for the report")
    ap.add_argument("--headed", action="store_true",
                    help="show the browser (same as HEADLESS=0)")
    args = ap.parse_args(argv)

    if args.headed:
        os.environ["HEADLESS"] = "0"
    name = args.name or (default.name if args.url == default.url else args.url)
    return replace(default, url=args.url, vendor=args.vendor, name=name)


def _launch_kwargs() -> dict:
    # HUMANIZE defaults OFF, same as tests/live-solve/solve_fixture.py, and for
    # the same measured reason. The solver already moves the mouse along its own
    # 60-point trajectory (`_smooth_move` -> `generate_trajectory`); camoufox's
    # humanize juggler then expands EACH of those 60 micro-moves into its own
    # humanised sub-trajectory — 60 nested traversals to cover one straight
    # line. On this demo page that is 25-52s per click round instead of ~5s,
    # which does not merely look slow: it blows the 120s overall_solve_timeout
    # and reports a solvable captcha as "not solved". What a vendor scores is
    # the trajectory SHAPE, and that comes from generate_trajectory either way.
    # Set HUMANIZE=1 to drive it the other way against a vendor that
    # fingerprints motion.
    kw = dict(headless=os.getenv("HEADLESS", "1") != "0",
              humanize=os.getenv("HUMANIZE", "0") != "0", geoip=False)
    # Point camoufox at YOUR fork's binary. If unset, camoufox uses its default
    # cached binary (`python -m camoufox fetch`).
    binary = os.getenv("CAMOUFOX_BINARY") or os.getenv("CAMOUFOX_EXECUTABLE_PATH")
    if binary:
        kw["executable_path"] = binary
    return kw


def _page_kwargs() -> dict:
    """Record the session when CAPTCHA_DEMO_VIDEO_DIR is set.

    Plain Playwright options, but they only produce a video on a build whose
    screencast actually emits frames. Stock camoufox accepts every one of them
    and writes either nothing or a blank 0.96s file — no error anywhere — so a
    caller that cares should check the size of what it gets back.
    """
    out = os.getenv("CAPTCHA_DEMO_VIDEO_DIR")
    if not out:
        return {}
    w, _, h = os.getenv("CAPTCHA_DEMO_VIDEO_SIZE", "1280x800").partition("x")
    return {"record_video_dir": out,
            "record_video_size": {"width": int(w), "height": int(h)}}


def _finish_video(page):
    """Close the context so the container is finalised; return the file path.

    Playwright writes the video on CONTEXT close, not page close — reading
    video.path() before that names a file which may never appear.
    """
    video = getattr(page, "video", None)
    if video is None:
        return None
    try:
        page.context.close()
        return video.path()
    except Exception:  # noqa: BLE001 — a missing video must not fail the demo
        return None


def _tokens(usage) -> tuple[int, int]:
    """(input, output) over a SolveResult's usage log.

    Accepts both spellings because the two ports report different ones and this
    is a demo, not a place to discover a key mismatch as a zero.
    """
    inp = out = 0
    for u in usage or []:
        inp += int(u.get("prompt_tokens", u.get("inputTokens", 0)) or 0)
        out += int(u.get("completion_tokens", u.get("outputTokens", 0)) or 0)
    return inp, out


def _explain(vendor: str, solved: bool, err) -> str:
    msg = str(err or "").lower()
    if "unsupported" in msg or "cannot solve" in msg:
        if vendor == "hcaptcha":
            return ("hCaptcha served a challenge type the solver does not handle "
                    "yet (drag / video / choose-the-card) — re-run for a grid.")
        return "Not a supported challenge type — re-run to try again."
    if "no captcha" in msg or "nocaptchafound" in msg:
        return ("No captcha widget was found on that page. Check the URL, or "
                "the widget may load only after an interaction.")
    if "vllm" in msg or "connection" in msg or "refused" in msg or "max retries" in msg:
        return ("Could not reach the vLLM server — is it up and is VLLM_BASE_URL "
                "correct? (A local server auto-starts only with captchakraken[serve].)")
    if "timeout" in msg:
        return "Timed out — the page or challenge never became interactable."
    if err:
        return str(err)
    if not solved:
        return ("The answer was submitted but the vendor did not accept it. Most "
                "often IP reputation or fingerprint flagging rather than a wrong "
                "answer — try a cleaner IP or a residential proxy.")
    return "Unknown failure."


def run_demo(spec: DemoSpec) -> None:
    spec = spec_from_argv(spec)

    t0 = time.time()
    ok = False
    reason = None
    inp = out = 0
    solve_s = 0.0
    video_path = None
    err = None

    try:
        with Camoufox(**_launch_kwargs()) as browser:
            page = browser.new_page(**_page_kwargs())
            try:
                page.goto(spec.url, wait_until="domcontentloaded", timeout=60_000)
                # The widget's iframe injects after DOMContentLoaded on every
                # vendor; the TS harness waits the same way.
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:  # noqa: BLE001 — a busy page is still solvable
                    pass
                page.wait_for_timeout(3000)

                s0 = time.time()
                try:
                    result = PageSolver().solve(page)
                finally:
                    solve_s = time.time() - s0
                ok = bool(result.is_solved)
                inp, out = _tokens(result.token_usage)
                if not ok:
                    reason = _explain(spec.vendor, ok, None)
            except CaptchaSolveError as e:
                err = e
                reason = _explain(spec.vendor, False, e)
            finally:
                # In the finally so a failed run still yields its recording —
                # that is the run you most want to watch back.
                video_path = _finish_video(page)
    except Exception as e:  # noqa: BLE001 — demo: report, don't traceback
        err = e
        reason = _explain(spec.vendor, False, e)

    tps = out / solve_s if out and solve_s > 0 else 0.0
    _report(spec, ok=ok, total_s=time.time() - t0, solve_s=solve_s,
            inp=inp, out=out, tps=tps, reason=reason, video=video_path)


def _fmt(s: float) -> str:
    return f"{s:.1f}s" if s >= 1 else f"{int(s * 1000)}ms"


def _report(spec, *, ok, total_s, solve_s, inp, out, tps, reason, video=None):
    line = "─" * 52
    print(f"\n{line}")
    print(f"  CaptchaKraken demo — {spec.name}")
    print(f"  {spec.url}")
    print(line)
    print(f"  result        : {'✓ SOLVED' if ok else '✗ not solved'}")
    print(f"  total time    : {_fmt(total_s)}   (solve: {_fmt(solve_s)})")
    print(f"  tokens        : {inp} in / {out} out")
    print(f"  gen speed     : {f'~{tps:.1f} tok/s' if tps > 0 else 'n/a'}")
    if video:
        print(f"  video         : {video}")
    if reason:
        print(f"  reason        : {reason}")
    print(f"{line}\n")

    # Optional machine-readable copy of the same report, appended as one JSON
    # line. The printed block is for a human reading a terminal; anything that
    # wants to keep the result — compare two adapters, chart tok/s over a
    # week — should not have to scrape it back out of that text.
    record = os.getenv("CAPTCHA_DEMO_RECORD")
    if record:
        with open(record, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "demo": spec.name, "url": spec.url, "vendor": spec.vendor,
                "ok": ok, "reason": reason,
                "total_s": round(total_s, 1), "solve_s": round(solve_s, 1),
                "input_tokens": inp, "output_tokens": out,
                "tokens_per_sec": round(tps, 1), "video": video,
            }) + "\n")

    sys.exit(0 if ok else 1)
