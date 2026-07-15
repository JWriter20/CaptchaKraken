# 🐙 CaptchaKraken

A self-hosted captcha solver for browser automation. Give it a page and it finds
the captcha, reads the image grid with a fine-tuned **Qwen3.5-9B** vision model,
and clicks through to a token. Everything runs on **your** hardware — no
third-party solving service involved.

> ⭐ **Enjoying CaptchaKraken? Star & watch the repo** for updates (smaller
> models, the hosted cloud API, new puzzle types):
> [**CaptchaKraken**](https://github.com/JWriter20/CaptchaKraken). One repo, two
> published ports — the TypeScript browser driver (npm: `captchakraken`) and
> the Python engine (PyPI: `captchakraken`). On GitHub: **Star** = ⭐ top-right,
> **Watch** = 👁 *Watch → All Activity* for release notifications.

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
| ✅ **hCaptcha click / drag puzzles** | Full-puzzle model → pixel click/drag actions |
| ✅ Cloudflare Turnstile | Works via the checkbox flow |
| ⛔ Video challenges | Skipped (detected upstream) |

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
**reCAPTCHA 3×3 — fast solve (~14 s)**

https://github.com/user-attachments/assets/e5e63787-1e5f-46e6-aa7e-f74af9962233

**reCAPTCHA 3×3 — multi-round refresh**

https://github.com/user-attachments/assets/0545f3ff-15cd-4bde-9b5d-bd3112bb032a

**reCAPTCHA 4×4 — one-shot "select all"**

https://github.com/user-attachments/assets/93b5dd43-c634-4644-8754-fb5f8ab8b9c9

**hCaptcha 3×3 grid — "select all" property puzzle (~15 s)**

https://github.com/user-attachments/assets/7df62269-d4fd-4ec8-9562-883409679ab6

**hCaptcha 3×3 grid — another property solve (~14 s)**

https://github.com/user-attachments/assets/8c7416fb-065c-4a0b-8f1b-f2f24fa88852

**hCaptcha 3×3 grid — property solve (~14 s)**

https://github.com/user-attachments/assets/f1b98e31-0162-4a3a-a188-bcf721475642
<!-- END DEMOS -->

### Beyond grids

Non-grid still-image puzzles — **click** ("click each …"), **drag**
("drag the piece into place"), path/connect, "choose the card" — route to the
full-puzzle model, which returns pixel-space click/drag actions the browser
replays. Only true **video** challenges are skipped (detected upstream). If the
model can't produce a usable action for a given frame, the solver fails fast
rather than guessing.

---

## Self-hosting

> 💡 **No GPU?** A hosted cloud API (no model to run) is coming — star the repo to
> be notified. For now CaptchaKraken runs on your own GPU or Apple-silicon machine.

### One-command install

```bash
./setup.sh
```

This checks your available memory, picks the right model size, downloads it plus
the grid model, and writes a `captchakraken.env` config file.

| Your memory | Model it picks |
|---|---|
| **≥ 22 GB** | `Qwen3.5-9B-FP8-dynamic` (8-bit) — best accuracy |
| **11–22 GB** | `Qwen3.5-9B-AWQ-4bit` (4-bit) — lighter, slightly less accurate |
| **< 11 GB** | Too small to run — the installer stops and explains your options |

If your hardware is too small, you can still `./setup.sh --download-only`
(e.g. to copy the model to a bigger server later), or watch the repo for smaller
models and the cloud API. Force a size with `./setup.sh --quant fp8|awq`.

### A note on speed

Solve time is dominated by how fast your hardware generates tokens, and LLM
generation is **memory-bandwidth bound** — each token streams the model's weights
through memory once. So speed tracks your GPU/SoC **memory bandwidth**, not its
capacity: roughly `tokens/sec ≈ bandwidth × ~50% ÷ bytes-per-token`, where the
8-bit (FP8) model reads ~9 GB/token and the 4-bit (AWQ) ~4.5 GB/token. (Measured
on a 5090: ~100 tok/s on FP8, ~200 tok/s on AWQ — both match this formula.)

Estimated throughput for common devices:

| Device | Memory bandwidth | FP8 (8-bit) | AWQ (4-bit) |
|---|---|---|---|
| NVIDIA H100 | ~3.35 TB/s | ~186 | ~370 |
| NVIDIA A100 | ~2.0 TB/s | ~111 | ~222 |
| **NVIDIA RTX 5090** | **~1.79 TB/s** | **~100** | **~200** |
| NVIDIA RTX 4090 | ~1.0 TB/s | ~56 | ~112 |
| NVIDIA RTX 5080 / 3090 | ~0.95 TB/s | ~53 | ~105 |
| NVIDIA RTX 5070 Ti | ~896 GB/s | ~50 | ~100 |
| AMD RX 7900 XTX | ~960 GB/s | ~53 | ~106 |
| NVIDIA RTX 3080 / 4080 | ~0.7–0.76 TB/s | ~40 | ~80 |
| Apple M2 / M3 Ultra | ~800 GB/s | ~44 | ~88 |
| NVIDIA RTX 5070 | ~672 GB/s | ~37 | ~74 |
| Apple M5 Max | ~614 GB/s | ~34 | ~68 |
| Apple M4 Max | ~546 GB/s | ~30 | ~60 |
| NVIDIA RTX 4070 | ~504 GB/s | ~28 ⚠️ | ~56 |
| Apple M1–M3 Max | ~400 GB/s | ~22 ⚠️ | ~44 |
| Apple M5 Pro | ~307 GB/s | ~17 ⚠️ | ~34 |
| Apple M4 Pro | ~273 GB/s | ~15 ⚠️ | ~30 |
| Apple M5 (base) | ~154 GB/s | ~8 ⚠️ | ~17 ⚠️ |

(Rough estimates at ~50% bandwidth efficiency; real numbers vary with batching,
KV-cache length, and driver. AWQ is faster but slightly less accurate.) Sources:
NVIDIA / AMD / Apple spec sheets.

**Cards that comfortably self-host** (≥ 30 tok/s): NVIDIA 5090 · 5080 · 5070 Ti ·
5070 · 4090 · 4080(Ti) · 3090 · 3080; AMD 7900 XTX / 7900 XT; Apple **Ultra**
chips and the **M4/M5 Max**. Most other cards fall under ~30 tok/s on the 8-bit
model — 4070 and below, older Apple **Max** chips (fine on the 4-bit model), Apple
**Pro**/base laptops, and older mid-range AMD.

> ⏳ **Below ~30 tokens/sec, self-hosting feels sluggish.** If that's your card,
> consider the upcoming **hosted cloud API** instead — it serves the 8-bit model
> at **~100 tokens/sec** with no GPU to run. ⭐ [Star the repo](https://github.com/JWriter20/CaptchaKraken)
> to be notified when it launches. `setup.sh` estimates your speed from your
> device's bandwidth (NVIDIA / AMD / Apple) and flags this automatically.

### Start the server (you usually don't have to)

The server is **hands-off**: after `setup.sh`, it **auto-starts on your first
solve** and stays up. You never run `vllm serve` yourself. To manage it
explicitly:

```bash
source captchakraken.env
captchakraken server start     # background, waits until healthy
captchakraken server status    # endpoint + which model is served
captchakraken server stop
```

Under the hood that runs the equivalent of (assembled from your env, so it
tracks whatever model you configured — keep `--enable-tower-connector-lora` or
the vision half of the LoRA is dropped and accuracy collapses):

```bash
vllm serve "$CAPTCHA_BASE_MODEL" \
  --reasoning-parser qwen3 \
  --enable-lora --enable-tower-connector-lora \
  --max-lora-rank 64 --max-model-len 65536 \
  --gpu-memory-utilization 0.80 --trust-remote-code \
  --port 8000 \
  --lora-modules "$CAPTCHA_LORA_NAME=$CAPTCHA_LORA_ADAPTER"
```

Defaults: base `Qwen/Qwen3.5-9B` (or a quantized variant `setup.sh` picks for
your VRAM) + the unified adapter `CaptchaKraken/CaptchaKraken_v1`, served
as `captcha`.

### Configuration

The solver only needs **two** environment variables (both written by
`setup.sh` into `captchakraken.env`):

| Variable | Meaning |
|---|---|
| `VLLM_BASE_URL` | Inference endpoint — your local vLLM server, or the hosted cloud endpoint when it launches. |
| `CAPTCHA_KRAKEN_API_KEY` | Bearer token — your server's key today, your account key on the cloud API later. |

---

## Usage

Two ports, pick your language. The **TypeScript** port (npm) is the browser
driver; the **Python** port (PyPI) is the engine + CLI (also usable standalone
to solve a screenshot). Both talk to the same vLLM server and are model-agnostic.

```bash
npm install captchakraken          # TypeScript browser driver
# or
pip install captchakraken              # Python engine + `captchakraken` CLI
```

Python one-liner (solve a screenshot → JSON click plan):

```bash
captchakraken path/to/captcha.png      # local server auto-starts on first call
```

The TypeScript solver does **not** launch the browser for you — you **bring your own**
browser and hand the solver a page. Install whichever automation framework you
prefer alongside it. The solver itself ships **no browser dependency**.

| Framework | Install | Pass to `solve()` |
|---|---|---|
| [Playwright](https://www.npmjs.com/package/playwright) (vanilla) | `npm i playwright` | the `page` directly |
| [Patchright](https://www.npmjs.com/package/patchright) (stealth Chromium) | `npm i patchright` | the `page` directly |
| [camoufox-js](https://www.npmjs.com/package/camoufox-js) (Firefox stealth) | `npm i camoufox-js` | the `page` directly |
| [Puppeteer](https://www.npmjs.com/package/puppeteer) | `npm i puppeteer` | `fromPuppeteer(page)` |

The first three are Playwright-compatible (they return a standard Playwright
`Page`), so you pass the page straight in. Puppeteer's API differs slightly, so
wrap its page once with `fromPuppeteer()`. All four are tested end-to-end against
the live reCAPTCHA demo.

In every example the solver reads `VLLM_BASE_URL` + `CAPTCHA_KRAKEN_API_KEY` from
the environment (run `setup.sh`, then `source captchakraken.env`), defaults to
the unified captcha LoRA, and `solve()` does detect → grid → click → verify.

### Playwright (vanilla)

```typescript
import { chromium } from 'playwright';
import { CaptchaKrakenSolver } from 'captchakraken';

const browser = await chromium.launch({ headless: false });
const page = await (await browser.newContext()).newPage();
await page.goto('https://www.google.com/recaptcha/api2/demo');

const solver = new CaptchaKrakenSolver();
await solver.solve(page);

await browser.close();
```

### Patchright (stealth-patched Chromium)

Drop-in for vanilla Playwright — same API, just a stealthier Chromium.

```typescript
import { chromium } from 'patchright';
import { CaptchaKrakenSolver } from 'captchakraken';

const browser = await chromium.launch({ headless: false });
const page = await (await browser.newContext()).newPage();
await page.goto('https://www.google.com/recaptcha/api2/demo');

const solver = new CaptchaKrakenSolver();
await solver.solve(page);

await browser.close();
```

### camoufox-js (Firefox stealth)

[`camoufox-js`](https://github.com/apify/camoufox-js) exposes a `Camoufox()`
launcher that returns a standard Playwright Browser. (On first run it may prompt
you to fetch its Firefox build: `npx camoufox-js fetch`.)

```typescript
import { Camoufox } from 'camoufox-js';
import { CaptchaKrakenSolver } from 'captchakraken';

const browser = await Camoufox({ headless: false });
const page = await (await browser.newContext()).newPage();
await page.goto('https://www.google.com/recaptcha/api2/demo');

const solver = new CaptchaKrakenSolver();
await solver.solve(page);

await browser.close();
```

### Puppeteer (via `fromPuppeteer`)

Puppeteer isn't Playwright-API-compatible, so wrap its page with the bundled
`fromPuppeteer()` adapter. Drive the **raw** Puppeteer page as usual (navigate,
etc.); only the object you hand `solve()` needs wrapping.

```typescript
import puppeteer from 'puppeteer';
import { CaptchaKrakenSolver, fromPuppeteer } from 'captchakraken';

const browser = await puppeteer.launch({ headless: false });
const page = await browser.newPage();
await page.goto('https://www.google.com/recaptcha/api2/demo');

const solver = new CaptchaKrakenSolver();
await solver.solve(fromPuppeteer(page));

await browser.close();
```

That's it — no model name to pass, no provider to choose, and **no browser locked
in**. The solver defaults to the unified captcha LoRA and the endpoint from your
env, and works with whatever browser you handed it.

### Cloning this repo

If you're cloning instead of installing from npm, initialize the submodule that
holds the detection/planner CLI:

```bash
git submodule update --init --recursive
npm install        # builds the solver + a local CLI venv (postinstall)
```

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
                    Qwen3.5-9B captcha LoRA on vLLM  ─▶  tile selection
                                   │
                    click plan ─▶ execute (human-like) ─▶ re-detect / verify
```

- **`find_grid`** finds the grid lines with plain OpenCV — no model needed. It
  lives in the Python port ([`python/`](python/), the `captchakraken` package).
- The **grid model** runs on your local vLLM server and says which tiles to click.
- The **browser driver** (the [`js/`](js/) TypeScript port) clicks, waits for
  reCAPTCHA's refreshing tiles, and keeps going until the captcha is solved. It
  shells out to the bundled Python engine for detection + planning.

---

## Roadmap

- ☁️ **Hosted cloud API** — solve over HTTP, no GPU required.
- 🪶 **Smaller / faster quantizations** so lower-VRAM hardware can self-host.
- 🧩 **Non-grid hCaptcha puzzles** — drag-and-drop, path, tetris-fit, and the
  other types currently detected-and-skipped.
- 🎯 **reCAPTCHA 4×4 robustness** — our weakest grid type end-to-end.
- 📈 More **real labeled data** for under-represented prompts.

> 📣 **Watch the repo to hear about these as they ship**, and ⭐ star if the
> project is useful to you — it genuinely helps:
> [**CaptchaKraken**](https://github.com/JWriter20/CaptchaKraken). Use GitHub's
> **Watch → All Activity** for release notifications.

---

## Repo layout

One repo, two published ports (like a mono-repo): the TypeScript browser driver
on npm, the Python engine on PyPI.

```
setup.sh          hardware-gated one-command setup (venv + vLLM + weights + env)
js/               TypeScript browser driver  →  npm: captchakraken
  src/            the solver; bundles the python engine at publish time
python/           the captchakraken package  →  PyPI: captchakraken
  src/captchakraken/   find_grid (OpenCV) + vLLM planner + server manager + CLI
  Dockerfile      build your own vLLM server image
.github/workflows/  CI (both ports) + publish (npm + PyPI)
```

---

## Migrating from v1

- The `apiProvider` / `model` / `apiKey` options for Gemini/OpenRouter/Ollama are
  **removed**. v2 talks only to a vLLM server.
- Set **`VLLM_BASE_URL`** and **`CAPTCHA_KRAKEN_API_KEY`** (or run `setup.sh`)
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
