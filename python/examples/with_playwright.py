"""
Vanilla Playwright + CaptchaKraken.

No adapter, and no browser dependency in the package: the driver duck-types the
Playwright surface, so a real `sync_playwright` page satisfies it as-is — as do
patchright's and camoufox's.

    pip install captchakraken playwright && playwright install chromium
    export VLLM_BASE_URL=https://api.captchakraken.com/v1
    export CAPTCHA_KRAKEN_API_KEY=ck_live_...
    python examples/with_playwright.py [url]

SYNC, NOT ASYNC. `PageSolver` mirrors the synchronous Playwright API. A sync
handle cannot be driven from inside an event loop, so `async_playwright` pages
cannot be solved — use `sync_playwright`, as here.
"""

import sys

from playwright.sync_api import sync_playwright

from captchakraken import PageSolver

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.google.com/recaptcha/api2/demo"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL)

        # ── the whole integration: construct, solve ──────────────────────────
        result = PageSolver().solve(page)
        # ─────────────────────────────────────────────────────────────────────

        print("✅ solved" if result.is_solved else "❌ not solved")
        browser.close()
        return 0 if result.is_solved else 1


if __name__ == "__main__":
    raise SystemExit(main())
