"""
Engine demo: run CaptchaKraken on Google's standard reCAPTCHA v2 demo page.

    pip install -e ".[serve]"     # engine + serving stack (or [.] against a remote server)
    pip install camoufox && python -m camoufox fetch   # or set CAMOUFOX_BINARY to your fork binary
    source ../captchakraken.env    # VLLM_BASE_URL + CAPTCHA_KRAKEN_API_KEY
    python examples/demoRecaptcha.py
"""

from _harness import DemoSpec, run_demo

if __name__ == "__main__":
    run_demo(DemoSpec(
        name="reCAPTCHA v2",
        url="https://www.google.com/recaptcha/api2/demo",
        vendor="recaptcha",
    ))
