"""
The auto-solver: install once, and captchas are solved as they appear.

`solve(page)` handles whatever is on the page right now. `watch(page)` is for
when you do not know WHERE in a script a challenge will interrupt you.

    python examples/watch_playwright.py [url] [seconds]

TWO SHAPES, because this port is synchronous:

    solver.watch(page).run()        # blocking — hold this page clean
    w = solver.watch(page)          # cooperative — you own the cadence
    while working():
        w.poll_once()

The TypeScript twin returns a background handle instead. That difference is
Playwright's, not ours: a sync handle is bound to the greenlet that created it,
so a worker thread cannot drive the page.

It injects nothing into the page on any launcher. Under camoufox its DOM reads
run in the isolated Juggler world by default.
"""

import sys

from playwright.sync_api import sync_playwright

from captchakraken import PageSolver

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.google.com/recaptcha/api2/demo"
SECONDS = int(sys.argv[2]) if len(sys.argv) > 2 else 30


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # ── install once, then let it work ───────────────────────────────────
        watcher = PageSolver().watch(
            page,
            on_solved=lambda r: print("✅ solved one:", r.is_solved),
            on_error=lambda e: print("solve failed:", e),
        )
        # ─────────────────────────────────────────────────────────────────────

        page.goto(URL)
        watcher.run(timeout_ms=SECONDS * 1000)   # blocks; solves as they appear

        print(f"stopped after {watcher.solves} solve(s)")
        browser.close()
        return 0 if watcher.solves else 1


if __name__ == "__main__":
    raise SystemExit(main())
