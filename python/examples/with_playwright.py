import sys

from playwright.sync_api import sync_playwright

from captchakraken import PageSolver

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.google.com/recaptcha/api2/demo"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL)

        result = PageSolver().solve(page)

        print("✅ solved" if result.is_solved else "❌ not solved")
        browser.close()
        return 0 if result.is_solved else 1


if __name__ == "__main__":
    raise SystemExit(main())
