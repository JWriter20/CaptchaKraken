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

from captchakraken.watcher import CaptchaWatcher

LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]

HTML = """
<body style="height:3000px">
  <div id="target" data-vendor="recaptcha">hello captcha</div>
  <div id="hidden" style="display:none">nope</div>
  <input id="field" />
  <iframe id="frame" srcdoc="<div id='inner'>inner text</div>"></iframe>
</body>
"""


def _installed_chromiums() -> List[str]:
    cache = Path.home() / ".cache" / "ms-playwright"
    rels = ("chrome-linux64/chrome", "chrome-linux/chrome",
            "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
            "chrome-win/chrome.exe")
    found = []
    for d in sorted(cache.glob("chromium-*"), reverse=True):
        for rel in rels:
            if (d / rel).exists():
                found.append(str(d / rel))
                break
    return found


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as p:
        attempts: List[dict] = [{}]
        attempts += [{"executable_path": exe} for exe in _installed_chromiums()]
        browser = None
        failures = []
        for kwargs in attempts:
            try:
                browser = p.chromium.launch(headless=True, args=LAUNCH_ARGS, **kwargs)
                break
            except Exception as exc:
                failures.append(f"{kwargs.get('executable_path', 'pinned build')}: "
                                f"{str(exc).splitlines()[0]}")
        if browser is None:
            pytest.skip("no launchable Chromium on this box; tried "
                        + "; ".join(failures))
        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 720})
            yield ctx.new_page()
        finally:
            browser.close()


def test_a_real_page_provides_every_member_the_driver_duck_types(page: Any) -> None:
    page.set_content(HTML)

    target = page.query_selector("#target")
    assert target is not None, "query_selector"
    assert len(page.query_selector_all("div")) >= 2, "query_selector_all"

    assert target.get_attribute("data-vendor") == "recaptcha", "get_attribute"
    assert (target.text_content() or "").strip() == "hello captcha", "text_content"
    assert target.is_visible() is True, "is_visible (visible element)"
    assert page.query_selector("#hidden").is_visible() is False, "is_visible (display:none)"
    assert target.bounding_box()["width"] > 0, "bounding_box"
    target.scroll_into_view_if_needed()
    assert len(target.screenshot()) > 0, "element screenshot"

    assert page.evaluate("() => document.title") == "", "evaluate"
    assert page.eval_on_selector("#target", "el => el.id") == "target", "eval_on_selector"
    assert page.viewport_size == {"width": 1280, "height": 720}, "viewport_size"

    frame = page.query_selector("#frame").content_frame()
    assert frame is not None, "content_frame"
    assert frame.query_selector("#inner") is not None, "frame.query_selector"
    assert frame.wait_for_selector("#inner", state="visible", timeout=5000), "frame.wait_for_selector"
    frame.wait_for_function("() => !!document.querySelector('#inner')", timeout=5000)

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


def test_one_watcher_covers_every_navigation_on_the_page(page: Any) -> None:
    solved: List[Any] = []

    class Solver:
        def detect_captcha(self, p: Any) -> Any:
            return p.query_selector("#c")

        def solve(self, p: Any) -> Any:
            p.eval_on_selector("#c", "el => el.remove()")
            solved.append(True)
            return {"is_solved": True}

    watcher = CaptchaWatcher(solver=Solver(), page=page, interval_ms=25)

    page.goto("data:text/html,<body>one</body>")
    watcher.run(timeout_ms=200)
    page.goto("data:text/html,<body>two</body>")
    watcher.run(timeout_ms=200)
    assert solved == [], "solved something on a page with no captcha"

    page.goto("data:text/html,<body><div id='c'></div>three</body>")
    watcher.run(timeout_ms=800)
    assert len(solved) == 1, "the watcher did not survive navigation"

    page.goto("data:text/html,<body><div id='c'></div>four</body>")
    watcher.run(timeout_ms=800)
    assert len(solved) == 2, "the watcher stopped arming after its first solve"
