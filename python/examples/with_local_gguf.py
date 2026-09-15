import os
import sys

from captchakraken import solve_captcha

TEXT_CAPTCHAS = {"--text"}


def main() -> int:
    args = [a for a in sys.argv[1:] if a not in TEXT_CAPTCHAS]
    if not args:
        print(__doc__)
        return 2
    image = args[0]
    text_mode = bool(TEXT_CAPTCHAS & set(sys.argv[1:]))

    print(f"endpoint : {os.getenv('VLLM_BASE_URL', '(default)')}")
    print(f"model    : {os.getenv('CAPTCHA_LORA_NAME', '(registry default)')}")
    print(f"prompts  : {os.getenv('CAPTCHA_LORA_ADAPTER', '(latest)')}")

    result = solve_captcha(image, text_mode=text_mode)

    print(f"answer   : {result}")
    return 0 if result else 1


if __name__ == "__main__":
    raise SystemExit(main())
