"""
Engine demo: run CaptchaKraken on the standard hCaptcha demo page.

    pip install -e ".[serve]"     # engine + serving stack (or [.] against a remote server)
    pip install camoufox && python -m camoufox fetch   # or set CAMOUFOX_BINARY to your fork binary
    source ../captchakraken.env    # VLLM_BASE_URL + CAPTCHA_KRAKEN_API_KEY
    python examples/demoHcaptcha.py                     # this demo page
    python examples/demoHcaptcha.py https://your.site/  # any URL

Note: hCaptcha randomly serves non-grid puzzles (drag / video / choose-the-card),
which the engine does not handle yet — the report says so; re-run for a grid.
"""

from _harness import DemoSpec, run_demo

if __name__ == "__main__":
    run_demo(DemoSpec(
        name="hCaptcha",
        url="https://accounts.hcaptcha.com/demo",
        vendor="hcaptcha",
    ))
