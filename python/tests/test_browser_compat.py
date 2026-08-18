"""
The Python driver's compatibility claim, checked against a REAL browser.

Twin of `js/src/browser-compat.test.ts`, and it exists for the same reason:
`test_page_solver.py` drives a fake page, which cannot catch Playwright
CHANGING one of the methods the driver calls. A fake happily keeps agreeing
with a driver that no longer matches the library.

The driver duck-types the Playwright surface and imports no browser package
(see page_solver.py's module docstring), so what is verified here is that a
real `sync_playwright` page actually provides every member that duck-typing
assumes — and that the watcher drives one end to end.

SKIPPED WHEN PLAYWRIGHT IS ABSENT, and deliberately not a dependency: this
package ships with no browser dependency at all. To run these:

    pip install playwright && playwright install chromium
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

sync_playwright = pytest.importorskip(
    "playwright.sync_api", reason="playwright not installed"
).sync_playwright

from captchakraken.watcher import CaptchaWatcher  # noqa: E402

LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]

HTML = """
<body style="height:3000px">
  <div id="target" data-vendor="recaptcha">hello captcha</div>
  <div id="hidden" style="display:none">nope</div>
  <input id="field" />
  <iframe id="frame" srcdoc="<div id='inner'>inner text</div>"></iframe>
</body>
"""


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=LAUNCH_ARGS)
        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 720})
            yield ctx.new_page()
        finally:
            browser.close()


def test_a_real_page_provides_every_member_the_driver_duck_types(page: Any) -> None:
    page.set_content(HTML)

    # Element lookup — by far the most-used call in the driver.
    target = page.query_selector("#target")
    assert target is not None, "query_selector"
    assert len(page.query_selector_all("div")) >= 2, "query_selector_all"

    # Element reads.
    assert target.get_attribute("data-vendor") == "recaptcha", "get_attribute"
    assert (target.text_content() or "").strip() == "hello captcha", "text_content"
    assert target.is_visible() is True, "is_visible (visible element)"
    assert page.query_selector("#hidden").is_visible() is False, "is_visible (display:none)"
    assert target.bounding_box()["width"] > 0, "bounding_box"
    target.scroll_into_view_if_needed()
    assert len(target.screenshot()) > 0, "element screenshot"

    # Page-level evaluation.
    assert page.evaluate("() => document.title") == "", "evaluate"
    assert page.eval_on_selector("#target", "el => el.id") == "target", "eval_on_selector"
    assert page.viewport_size == {"width": 1280, "height": 720}, "viewport_size"

    # The iframe path — how every real captcha is reached.
    frame = page.query_selector("#frame").content_frame()
    assert frame is not None, "content_frame"
    assert frame.query_selector("#inner") is not None, "frame.query_selector"
    assert frame.wait_for_selector("#inner", state="visible", timeout=5000), "frame.wait_for_selector"
    frame.wait_for_function("() => !!document.querySelector('#inner')", timeout=5000)

    # Input.
    page.mouse.move(100, 100, steps=4)
    page.mouse.down(button="left")
    page.mouse.up(button="left")
    page.focus("#field")
    page.keyboard.type("abc", delay=1)
    assert page.eval_on_selector("#field", "el => el.value") == "abc", "keyboard.type"
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    assert page.eval_on_selector("#field", "el => el.value") == "", "select-all + delete"

    assert page.is_closed() is False, "is_closed"


def test_the_watcher_solves_a_captcha_that_appears_after_it_is_installed(page: Any) -> None:
    page.set_content("<body></body>")
    solved: List[Any] = []

    class Solver:
        def detect_captcha(self, p: Any) -> Any:
            return p.query_selector("#late-captcha")

        def solve(self, p: Any) -> Any:
            # Removing it is what a real solve does to the challenge; the next
            # probe must then find nothing, or the watcher re-solves forever.
            p.eval_on_selector("#late-captcha", "el => el.remove()")
            solved.append(True)
            return {"is_solved": True}

    watcher = CaptchaWatcher(solver=Solver(), page=page, interval_ms=25)

    page.evaluate(
        "() => setTimeout(() => {"
        "  const el = document.createElement('div');"
        "  el.id = 'late-captcha';"
        "  document.body.appendChild(el);"
        "}, 100)"
    )

    watcher.run(timeout_ms=1500)

    assert len(solved) == 1, f"solved {len(solved)} times, expected exactly once"


def test_poll_once_drives_a_real_page_without_blocking(page: Any) -> None:
    """The cooperative shape: a caller with their own loop calls poll_once()."""
    page.set_content('<body><div id="late-captcha"></div></body>')

    class Solver:
        def detect_captcha(self, p: Any) -> Any:
            return p.query_selector("#late-captcha")

        def solve(self, p: Any) -> Any:
            p.eval_on_selector("#late-captcha", "el => el.remove()")
            return {"is_solved": True}

    watcher = CaptchaWatcher(solver=Solver(), page=page, interval_ms=10_000)

    started = time.monotonic()
    assert watcher.poll_once() is not None, "did not solve the captcha already on the page"
    assert watcher.poll_once() is None, "solved twice — the challenge was gone"
    assert time.monotonic() - started < 2.0, "poll_once waited an interval; run() owns the cadence"
