import sys

from playwright.sync_api import sync_playwright

from captchakraken import PageSolver

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.google.com/recaptcha/api2/demo"
SECONDS = int(sys.argv[2]) if len(sys.argv) > 2 else 30


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        watcher = PageSolver().watch(
            page,
            on_solved=lambda r: print("✅ solved one:", r.is_solved),
            on_error=lambda e: print("solve failed:", e),
        )

        page.goto(URL)
        watcher.run(timeout_ms=SECONDS * 1000)

        print(f"stopped after {watcher.solves} solve(s)")
        browser.close()
        return 0 if watcher.solves else 1


if __name__ == "__main__":
    raise SystemExit(main())
