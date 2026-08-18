# captchakraken

The Python engine + CLI behind [CaptchaKraken](https://github.com/JWriter20/CaptchaKraken):
OpenCV grid detection + a fine-tuned **Qwen3.5-9B** vision LoRA served on
**vLLM**. Given a screenshot of a captcha grid, it locates the tiles and returns
the click plan. Ships the `captchakraken` command.

> For demo videos, accuracy numbers, the browser driver, and the full
> self-hosting guide, see the main repo
> **[CaptchaKraken](https://github.com/JWriter20/CaptchaKraken)**.

## Install

```bash
pip install captchakraken            # client: OpenCV detection + vLLM HTTP planner
pip install "captchakraken[serve]"   # + the serving stack (vLLM/torch) to self-host
```

The base install is lightweight — everything you need to solve captchas against
a vLLM server (local or remote). The `[serve]` extra pulls the heavy stack only
if you want to run the model yourself. The one-command
[`setup.sh`](https://github.com/JWriter20/CaptchaKraken) installs `[serve]`,
downloads the weights, and writes an env file for you.

## Hands-off server

The vLLM server is managed for you. On your first solve, if the configured
endpoint is **local** and nothing is listening, a server is started
automatically and reused. Point `VLLM_BASE_URL` at a server you already run to
skip local management entirely.

```bash
captchakraken server start | stop | status | run
```

## Usage

```bash
# Solve an image/video: classify → find_grid → plan. Prints the click actions.
captchakraken path/to/captcha.png
captchakraken path/to/captcha.png --puzzle-source hcaptcha
```

```python
from captchakraken import CaptchaSolver

solver = CaptchaSolver()          # connects to / auto-starts a local vLLM
actions = solver.solve("captcha.png")
```

Pure-OpenCV tool subcommands (no model): `find-grid`, `find-checkbox`,
`detect-selected`, `grid-cell-states`, `find-move`, `find-movable`, and a
persistent `serve` worker the browser driver polls.

## Configuration (model-agnostic)

Everything model-specific lives in `captchakraken.config` and is env-overridable
— the solver never hard-codes a model.

| Variable | Meaning | Default |
|---|---|---|
| `VLLM_BASE_URL` | Inference endpoint | `http://localhost:8000/v1` |
| `CAPTCHA_KRAKEN_API_KEY` | Bearer token (`VLLM_API_KEY` also accepted) | `EMPTY` |
| `CAPTCHA_BASE_MODEL` | Base weights vLLM loads | `Qwen/Qwen3.5-9B` |
| `CAPTCHA_LORA_ADAPTER` | Captcha adapter (HF id or path) | `CaptchaKraken/CaptchaKraken_v1` |
| `CAPTCHA_LORA_NAME` | Served adapter name the client requests | `captcha` |
| `CAPTCHA_KRAKEN_AUTOSTART` | `0` disables local auto-start | `1` |

## License

**CaptchaKraken Source-Available License v1.1** — see [LICENSE](./LICENSE).
Build *with* it (scrapers, QA tooling) and run it against **any** browser you
like, stealth or not. You may **not sell the solve itself**, ship a thin wrapper
(browser extension, hosted solving API), or **bundle it as a built-in feature of
a stealth/antidetect browser you distribute** — using it with one is fine. Those
three are licensable, not categorically refused: open an issue to ask.
