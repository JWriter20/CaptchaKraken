"""Solve one captcha image against a LOCAL GGUF backend.

No browser and no GPU required — this is the shortest way to prove your Ollama,
LM Studio or llama.cpp server is wired up correctly before you point a real
browser at it.

    pip install captchakraken

Ollama:

    OLLAMA_CONTEXT_LENGTH=16384 ollama serve
    ollama run hf.co/CaptchaKraken/CaptchaKraken-v1.2-GGUF:Q4_K_M

    export VLLM_BASE_URL=http://localhost:11434/v1
    export CAPTCHA_KRAKEN_API_KEY=ollama
    export CAPTCHA_LORA_NAME=hf.co/CaptchaKraken/CaptchaKraken-v1.2-GGUF:Q4_K_M

LM Studio — start the server from the Developer tab, then:

    export VLLM_BASE_URL=http://localhost:1234/v1
    export CAPTCHA_KRAKEN_API_KEY=lm-studio
    export CAPTCHA_LORA_NAME=captchakraken-v1.2-gguf   # whatever it lists

Either way, also:

    export CAPTCHA_LORA_ADAPTER=CaptchaKraken/CaptchaKraken-v1.2-GGUF
    python examples/with_local_gguf.py path/to/captcha.png

TWO NAMES, AND THEY ARE DIFFERENT THINGS. `CAPTCHA_LORA_NAME` is the `model`
field that goes on the wire, so it has to match whatever your server calls the
model. `CAPTCHA_LORA_ADAPTER` is the published repo id, and it is how the client
picks the prompts and the image resolution these weights expect — neither can be
read off a local model name, and getting them wrong costs accuracy silently
rather than erroring.

RAISE THE CONTEXT LENGTH. Animated challenges arrive as several stills in one
request. Ollama defaults to 4k tokens under 24 GB of VRAM, which does not fit
them: still puzzles work, animated ones fail.
"""

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
    # A distorted-text captcha answers with a STRING, not a place on the
    # picture, and that is the one thing a bare image cannot tell the solver.
    text_mode = bool(TEXT_CAPTCHAS & set(sys.argv[1:]))

    print(f"endpoint : {os.getenv('VLLM_BASE_URL', '(default)')}")
    print(f"model    : {os.getenv('CAPTCHA_LORA_NAME', '(registry default)')}")
    print(f"prompts  : {os.getenv('CAPTCHA_LORA_ADAPTER', '(latest)')}")

    result = solve_captcha(image, text_mode=text_mode)

    print(f"answer   : {result}")
    return 0 if result else 1


if __name__ == "__main__":
    raise SystemExit(main())
