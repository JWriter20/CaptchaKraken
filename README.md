# 🐙 CaptchaKraken

A self-hosted captcha solver for browser automation. Give it a page and it finds
the captcha, reads the image grid with a fine-tuned **Qwen3.5-9B** vision model,
and clicks through to a token. Everything runs on **your** hardware — no
third-party solving service involved.

> ⭐ If this is useful, please star the repo.

> **⚠️ v2 is a breaking change.** It's a full rewrite. The old multi-provider
> setup (Gemini / OpenRouter / Ollama) is gone — v2 runs one purpose-built grid
> model on a local **vLLM** server. Upgrading from v1? See
> [Migrating from v1](#migrating-from-v1).

---

## What it can solve today

CaptchaKraken handles **image-grid** captchas — the "select all squares with…"
challenges. It detects the captcha, solves the grid, clicks, and verifies.

| Captcha type | Status |
|---|---|
| ✅ Checkbox / "I'm not a robot" | Works end-to-end |
| ✅ **reCAPTCHA 3×3** (dynamic) | Works end-to-end |
| ✅ **reCAPTCHA 4×4** (one-shot) | Works end-to-end |
| ✅ **hCaptcha 3×3 image grid** | Works end-to-end |
| ✅ Cloudflare Turnstile | Works via the checkbox flow |

**How accurate is the model?** On our hand-labeled real captcha set, the model
picks the exactly-correct tiles **94.7%** of the time for reCAPTCHA 3×3 (86.7%
for hCaptcha, 76.2% for the harder 4×4 grid — **85.8%** overall).

**What about real solve rates in a browser?** They vary a lot depending on your
**IP reputation and browser setup** (see [below](#rate-limiting--ip-reputation)).
On a standard setup, expect **roughly 50%** end-to-end for reCAPTCHA. A clean IP
and good stealth browser do better; a flagged IP does much worse — providers
reject even correct answers once they distrust your IP.

### Demo videos

Live solves, recorded straight from the browser:

<!-- BEGIN DEMOS -->
- ▶️ [reCAPTCHA 3×3 — fast solve (~14 s)](docs/demos/recaptcha_3x3_demo_1.mp4)
- ▶️ [reCAPTCHA 3×3 — multi-round refresh](docs/demos/recaptcha_3x3_demo_2.mp4)
- ▶️ [reCAPTCHA 4×4 — one-shot "select all"](docs/demos/recaptcha_4x4_demo_1.mp4)
<!-- END DEMOS -->

### Not supported yet

hCaptcha also serves non-grid puzzles (drag, path, "choose the card", etc.).
CaptchaKraken **detects and skips** these instead of guessing — they're on the
[roadmap](#roadmap).

---

## Self-hosting

> 💡 **No GPU?** A hosted cloud API (no model to run) is coming — star the repo to
> be notified. For now CaptchaKraken runs on your own GPU or Apple-silicon machine.

### One-command install

```bash
bash install.sh
```

This checks your available memory, picks the right model size, downloads it plus
the grid model, and writes a `captchakraken.env` config file.

| Your memory | Model it picks |
|---|---|
| **≥ 22 GB** | `Qwen3.5-9B-FP8-dynamic` (8-bit) — best accuracy |
| **11–22 GB** | `Qwen3.5-9B-AWQ-4bit` (4-bit) — lighter, slightly less accurate |
| **< 11 GB** | Too small to run — the installer stops and explains your options |

If your hardware is too small, you can still `bash install.sh --download-only`
(e.g. to copy the model to a bigger server later), or watch the repo for smaller
models and the cloud API. Force a size with `bash install.sh --quant fp8|awq`.

### Start the server

`install.sh` prints the exact `vllm serve …` command for your model. Keep the
`--enable-tower-connector-lora` flag — without it the vision part of the model is
dropped and accuracy falls apart.

```bash
source captchakraken.env
export VLLM_API_KEY="$CAPTCHA_KRAKEN_API_KEY"   # server bearer == solver key
vllm serve "RedHatAI/Qwen3.5-9B-FP8-dynamic" \
  --reasoning-parser qwen3 \
  --enable-lora --enable-tower-connector-lora \
  --max-lora-rank 64 --max-model-len 65536 \
  --gpu-memory-utilization 0.80 --trust-remote-code \
  --port 8000 \
  --lora-modules captcha-grid=JobHarvest/qwen3.5-9b-grid-lora
```

### Configuration

The solver only needs **two** environment variables (both written by
`install.sh` into `captchakraken.env`):

| Variable | Meaning |
|---|---|
| `VLLM_BASE_URL` | Inference endpoint — your local vLLM server, or the hosted cloud endpoint when it launches. |
| `CAPTCHA_KRAKEN_API_KEY` | Bearer token — your server's key today, your account key on the cloud API later. |

---

## Usage

```bash
npm install playwright-captcha-kraken-js
```

If you're cloning this repo, initialize the submodule that holds the
detection/planner CLI:

```bash
git submodule update --init --recursive
npm install        # builds the solver + a local CLI venv (postinstall)
```

```typescript
import { Camoufox } from '@jobharvest/camoufox-js';
import { CaptchaKrakenSolver } from 'playwright-captcha-kraken-js';

const browser = await Camoufox({ headless: false });
const page = await (await browser.newContext()).newPage();
await page.goto('https://www.google.com/recaptcha/api2/demo');

// Reads VLLM_BASE_URL + CAPTCHA_KRAKEN_API_KEY from the environment.
const solver = new CaptchaKrakenSolver();
await solver.solve(page);   // detect → solve grid → click → verify

await browser.close();
```

That's it — no model name to pass, no provider to choose. The solver defaults to
the published grid LoRA and the endpoint from your env.

### Rate limiting & IP reputation

> ⚠️ **Solving many captchas fast from one IP lowers your success rate.** This is
> normal anti-abuse behavior — once a provider distrusts an IP, it rejects
> submissions **even when the answer is correct** and serves harder challenges.

CaptchaKraken only produces the answer. Managing your IP reputation is **your
job**. In production you'll usually want to:

- **Use rotating / residential proxies** instead of one IP.
- **Space out requests** — avoid rapid bursts.
- **Rotate the IP** when you notice correct answers being rejected or challenges
  getting harder, rather than retrying on the same one.

This only affects whether the provider *accepts* a solve — it doesn't change the
model's accuracy.

---

## How it works

```
browser ─▶ detect captcha ─▶ screenshot frame
                                   │
                    OpenCV find_grid (color-agnostic line tracer)
                                   │  tile boxes
                                   ▼
                    Qwen3.5-9B grid LoRA on vLLM  ─▶  tile selection
                                   │
                    click plan ─▶ execute (human-like) ─▶ re-detect / verify
```

- **`find_grid`** finds the grid lines with plain OpenCV — no model needed. It's
  in the [`CaptchaKraken-cli`](CaptchaKraken-cli/) submodule.
- The **grid model** runs on your local vLLM server and says which tiles to click.
- The **solver** (this repo) drives the browser: it clicks, waits for reCAPTCHA's
  refreshing tiles, and keeps going until the captcha is solved.

---

## Roadmap

- ☁️ **Hosted cloud API** — solve over HTTP, no GPU required.
- 🪶 **Smaller / faster quantizations** so lower-VRAM hardware can self-host.
- 🧩 **Non-grid hCaptcha puzzles** — drag-and-drop, path, tetris-fit, and the
  other types currently detected-and-skipped.
- 🎯 **reCAPTCHA 4×4 robustness** — our weakest grid type end-to-end.
- 📈 More **real labeled data** for under-represented prompts.

---

## Repo layout

```
install.sh                          hardware-gated one-command setup
LICENSE                             source-available (see "License")
CONTRIBUTING.md                     how to contribute + dev setup
src/                                the browser solver (TypeScript)
tests/record_demos.spec.ts          live-solve recorder (numbers + demos)
captcha_videos/                     curated demo clips embedded above
CaptchaKraken-cli/                  find_grid + vLLM grid planner (Python submodule)
```

---

## Migrating from v1

- The `apiProvider` / `model` / `apiKey` options for Gemini/OpenRouter/Ollama are
  **removed**. v2 talks only to a vLLM server.
- Set **`VLLM_BASE_URL`** and **`CAPTCHA_KRAKEN_API_KEY`** (or run `install.sh`)
  instead of provider API keys.
- `new CaptchaKrakenSolver()` now needs no model/provider — it defaults to the
  grid LoRA.
- v1's `transformers` / `torch` / SAM3 dependencies are gone from the solver venv;
  they live on the `v1-old-architecture` branch if you need them.

---

## Contributing

PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). CI runs hermetic
grid-detection tests + a TypeScript build on every PR (no GPU/network). Release
notes are in [CHANGELOG.md](CHANGELOG.md).

## License

Source-available under the **CaptchaKraken Source-Available License** — see
[LICENSE](LICENSE). In short:

- ✅ **Allowed:** personal use, research, and commercial use *inside a larger
  product that adds value beyond captcha solving itself* — web scrapers, stealth
  browsers, data-collection pipelines, QA tooling.
- ⛔ **Not allowed:** selling captcha-solving as a service, "thin wrapper"
  products (browser extensions, hosted endpoints, CLIs) whose main purpose is
  solving, or relaying the model's outputs through a paid solving API.

**Build *with* it; don't sell *the solve*.** For prohibited uses, contact us
about a commercial license.

---

> ⚠️ Use responsibly and lawfully — respect the terms of service of any site you
> interact with. This project is for legitimate automation, research, and
> testing.
