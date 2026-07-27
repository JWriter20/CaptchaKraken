<h1 align="center">🐙 CaptchaKraken</h1>

<p align="center">
  <b>A self-hosted captcha solver for browser automation.</b><br>
  Give it a page — it finds the captcha, reads the puzzle with a fine-tuned
  <b>Qwen3.5-9B</b> vision model, and clicks through to a token.<br>
  Everything runs on <b>your</b> hardware. No third-party solving service.
</p>

<p align="center">
  <a href="https://www.npmjs.com/package/captchakraken"><img src="https://img.shields.io/npm/v/captchakraken?logo=npm&label=npm" alt="npm"></a>
  <a href="https://pypi.org/project/captchakraken/"><img src="https://img.shields.io/pypi/v/captchakraken?logo=pypi&logoColor=white&label=PyPI" alt="PyPI"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-Source--Available-blue" alt="License: Source-Available"></a>
  <a href="https://github.com/JWriter20/CaptchaKraken/actions/workflows/ci.yml"><img src="https://github.com/JWriter20/CaptchaKraken/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

> ⭐ **Enjoying CaptchaKraken?** [Star & watch the repo](https://github.com/JWriter20/CaptchaKraken)
> for new puzzle types, smaller models, and the hosted cloud API. One repo, two
> published ports — the TypeScript browser driver (**npm:** `captchakraken`) and
> the Python engine (**PyPI:** `captchakraken`).

---

## What it solves today

CaptchaKraken detects the captcha, solves it, clicks, and verifies — end to end.

| Captcha type | Status |
|---|---|
| ✅ Checkbox / "I'm not a robot" | Works end-to-end |
| ✅ **reCAPTCHA 3×3** (dynamic refresh) | Works end-to-end |
| ✅ **reCAPTCHA 4×4** (one-shot) | Works end-to-end |
| ✅ **hCaptcha 3×3 image grid** | Works end-to-end |
| ✅ **hCaptcha click / drag puzzles** | Full-puzzle model → pixel click/drag actions |
| ✅ Cloudflare Turnstile | Works via the checkbox flow |
| ⛔ Video challenges | **Abyss** only — the open weights skip them ([roadmap](./docs/roadmap.md)) |

### Accuracy, measured

Measured **2026-07-27** against the deployed `CaptchaKraken_v1.1` adapter, over
the full customer path — HTTPS → gateway → vLLM — rather than against a local
checkpoint. Exact set match: every correct tile and no incorrect ones, no
partial credit, because a partially-correct grid answer is a rejected captcha.

Test images that also appear in a labeled-train augment pool are **excluded**
(878 of them, including 313 reCAPTCHA 4×4), the same exclusion `grade.py`
applies — so these are puzzles the adapter has not been trained on.

| Challenge | n | Exact match | 95% CI |
|---|---:|---:|---:|
| reCAPTCHA 4×4 | 100 | **97.0%** | 91.5–99.0% |
| reCAPTCHA 3×3 | 100 | **96.0%** | 90.2–98.4% |
| hCaptcha 3×3 property | 100 | **94.0%** | 87.5–97.2% |
| **Overall** | **300** | **95.7%** | **92.7–97.5%** |

Median latency 1.36 s end to end including the network (p10 0.84 s, p90
1.76 s), median prompt 341 tokens. Of the 13 failures, 5 differ from the
ground truth by exactly one tile.

> **Grids are sent with the cell numbers drawn on.** `solver.py` runs
> `find_grid`, renders `get_numbered_grid_overlay` (red labels, top-right, all
> cells 1..N — byte-identical to the overlay the training data was built with)
> and sends *that*. This is not cosmetic: measured on raw un-numbered
> screenshots the same model scores **0% on 4×4**, because it has to invent a
> numbering convention for sixteen cells of one continuous photograph and
> answers `1..k`. If you are building your own client, draw the overlay.

Method, per-device speed tables, and why browser solve rates differ from model
accuracy: **[docs/performance.md](./docs/performance.md)**.

---

## The models

Three models, named for the ocean's depth zones. Depth means capability, and it
also means access: at the surface the weights are yours to download, and at the
bottom the model is not published at all.

| | Depth | What it is | Weights |
|---|---|---|---|
| 🟦 **Sunlight** | 0–200 m | The adapter merged into the base and quantised to **4-bit (AWQ)**. ~6 GB, comfortable on 11 GB of VRAM. | Public *(planned)* |
| 🟦 **Twilight** | 200–1,000 m | The same merge at **8-bit (FP8)**. ~14 GB, wants 22 GB to serve comfortably. The most accurate weights we publish. | Public *(planned)* |
| ⬛ **Abyss** | 4,000–6,000 m | What the hosted API runs. Trained against the failures of the open weights, and the only one that handles **video challenges**. | **Never published** |

All three descend from one training run, which produces the public LoRA adapter
[`CaptchaKraken/CaptchaKraken_v1`](https://huggingface.co/CaptchaKraken/CaptchaKraken_v1).
That adapter is what `setup.sh` installs today: it is applied at serve time on
top of a stock `Qwen3.5-9B-VL`, which means two downloads and an
`--enable-lora` flag.

**Sunlight and Twilight exist so you do not have to do that.** They are the
adapter already merged into the base and quantised, so what you download is one
self-contained model that vLLM, Ollama, SGLang or plain `transformers` will
load without knowing what a LoRA is.

> ⚠️ **Sunlight and Twilight are not on HuggingFace yet.** The merges are
> specified and the ids reserved; the weights are not uploaded. Until they are,
> `./setup.sh` installs the LoRA and a matching quantised base, which is the
> same model in two files. This note comes down when they ship.

**Abyss is hosted-only on purpose, and it is not a bigger quantisation of the
public weights.** Every puzzle the open model gets wrong on the held-out set is
a labelled example of a weakness, and Abyss is trained specifically to close
them — starting with the non-grid hCaptcha puzzles and video, which the open
weights skip rather than guess at. Keeping it on our own fleet is what lets it
keep learning from production failures without shipping a customer's puzzle set
to everyone who runs `hf download`.

Which one you want:

| If | Use |
|---|---|
| You have no GPU | **Abyss**, via the hosted API |
| You have 22 GB+ of VRAM | **Twilight** |
| You have 11–22 GB | **Sunlight** |
| You already serve your own Qwen3.5-9B-VL | the **LoRA adapter** |
| You need video challenges | **Abyss** — the open weights skip them rather than guess |

---

## Solves

### What a solve actually looks like

One inference round. The screenshot goes up, cell numbers come back — that is
the whole protocol, and it is why anything that speaks the OpenAI
chat-completions API can drive this.

Every grid request carries the screenshot **with the cell numbers drawn on it**
by `get_numbered_grid_overlay` — see the note under Accuracy. The model reads
those labels; it does not infer a numbering.

**reCAPTCHA 3×3** — nine separate photographs, pick the matching ones:

```jsonc
// prompt (abbreviated) ────────────────────────────────────────────────
"Solve the captcha grid by choosing the cell numbers that match the
 description from the captcha image prompt.

 Grid: 3x3 (9 cells)
 Hint: Separate images. Select only clear matches.
 ...
 Return JSON Array: [list of cell numbers (1-9)]"

// response ────────────────────────────────────────────────────────────
[2, 8, 9]
// ground truth [2, 8, 9] ✓   1,796 ms   336 prompt / 10 completion tokens
```

**hCaptcha 3×3 property puzzle** — same shape, different vendor:

```jsonc
[3, 5, 8]
// ground truth [3, 5, 8] ✓   958 ms   348 prompt / 10 completion tokens
```

**reCAPTCHA 4×4** — one large image cut into sixteen tiles, "select ALL parts":

```jsonc
[5, 9, 12, 16]
// ground truth [5, 9, 12, 16] ✓   1,385 ms   341 prompt / 15 completion tokens
```

The browser driver wraps this: it finds the grid, converts cell numbers into
click coordinates, clicks, waits for tiles to refresh, and re-solves until the
challenge clears. What the CLI hands back to it is bounding boxes rather than
cell indices:

```jsonc
{
  "actions": {
    "action": "click",
    "target_bounding_boxes": [
      [0.010, 0.206, 0.335, 0.418],
      [0.335, 0.206, 0.660, 0.418],
      [0.335, 0.631, 0.660, 0.843]
    ]
  },
  "token_usage": [{ "prompt_tokens": 348, "completion_tokens": 10 }]
}
```

### End-to-end, in a real browser

Both runs below drove **live reCAPTCHA challenges** at
`google.com/recaptcha/api2/demo` on 2026-07-27, headless, against the hosted
API. A "solve" means reCAPTCHA accepted and the widget cleared — not that the
model's first answer was right.

| Browser | Solved | Rounds billed | Median wall clock |
|---|---:|---:|---:|
| [Camoufox](https://camoufox.com) 0.4.11 | **3 / 3** | 21 | 96 s |
| Holo 152.0.3 | **1 / 3** | 21 | 85 s |

A solve took 5–8 model rounds, because reCAPTCHA replaces tiles after each
click and every replacement is a fresh puzzle. That is the whole reason the
hosted API meters per round rather than per solve.

**Solve rate in a browser is not model accuracy, and the gap is mostly your
IP.** reCAPTCHA rejects correct answers from addresses it distrusts, and both
runs above came from the same datacenter address in the same hour — which is
the least favourable condition there is, and the likeliest explanation for the
difference between the two rows. Neither figure is a benchmark; they are proof
the path works. See
[Rate limiting & IP reputation](./docs/performance.md#rate-limiting--ip-reputation).

Reproduce either with the live-solve harness:

```bash
cd tests/live-solve                       # in the CaptchaKrakenFinetune repo
VLLM_BASE_URL=https://api.captchakraken.com/v1 \
VLLM_API_KEY=ck_live_… \
TARGET=recaptcha TRIALS=3 npm run solve
```

---

## Quickstart

**1. Self-host the model** (one command — detects your GPU/Apple-silicon memory,
picks a model size that fits, downloads it, and writes a config file):

```bash
./setup.sh
```

`setup.sh` installs the LoRA plus a base quantised to fit your card — 8-bit
above 22 GB of VRAM, 4-bit above 11 GB. When **Sunlight** and **Twilight** ship
it will offer those single-file merges instead. Full hardware guide:
**[docs/self-hosting.md](./docs/self-hosting.md)**.

> 💡 **No GPU?** Use the hosted API and run **Abyss** — no weights, no vLLM, no
> card. Sign in with GitHub at
> **[captchakraken.com](https://captchakraken.com/signin)** and you get a key
> and free credits immediately; billing is per inference round. Then skip to
> step 2 with `VLLM_BASE_URL=https://api.captchakraken.com/v1` and your
> `ck_live_…` key.

**2. Solve.** The server auto-starts on your first solve — nothing else to run.

```bash
source captchakraken.env
captchakraken path/to/captcha.png     # Python: screenshot → JSON click plan
```

In the browser (TypeScript) — bring your own Playwright/Puppeteer page:

```typescript
import { chromium } from 'playwright';
import { CaptchaKrakenSolver } from 'captchakraken';

const page = await (await (await chromium.launch()).newContext()).newPage();
await page.goto('https://www.google.com/recaptcha/api2/demo');

await new CaptchaKrakenSolver().solve(page);   // detect → solve → click → verify
```

Install the port you need and see every framework (Playwright, Patchright,
camoufox-js, Puppeteer) in **[docs/usage.md](./docs/usage.md)**:

```bash
npm install captchakraken        # TypeScript browser driver
pip install captchakraken        # Python engine + `captchakraken` CLI
```

**Stay current** — pull the latest model + engine in one step, no reinstall:

```bash
captchakraken fetch              # latest LoRA from HuggingFace + vLLM upgrade + restart
```

---

## Documentation

Most of the detail lives in the docs hub — start at **[docs/](./docs/README.md)**.

| Guide | What's in it |
|---|---|
| 📦 [Self-hosting](./docs/self-hosting.md) | `setup.sh`, model sizes, server management, config, **updating** |
| 🚀 [Usage](./docs/usage.md) | Install, the 4 browser frameworks, the Python CLI, migrating from v1 |
| ⚙️ [How it works](./docs/how-it-works.md) | The solve pipeline, `find_grid`, the freshness guard, dedup |
| 📊 [Performance](./docs/performance.md) | Accuracy, speed-by-device tables, IP-reputation & rate limits |
| 🗺️ [Roadmap](./docs/roadmap.md) | Video support, more captcha types, and what shipped |
| 📜 [Licensing](./docs/licensing.md) | Plain-English: what you can and can't build |

---

## Roadmap

- 🟢 **Shipped** — the **hosted API** (`api.captchakraken.com`, self-serve
  signup), a mid-inference **freshness guard** (never act on a stale frame),
  and a one-command **`captchakraken fetch`** updater.
- 🟡 **In progress** — 🎥 **video challenges** (Abyss), publishing the
  **Sunlight** and **Twilight** merges.
- ⚪ **Planned** — 🧩 more non-grid hCaptcha puzzles (drag, path, tetris-fit).

The visual, always-current version is in **[docs/roadmap.md](./docs/roadmap.md)**.
📣 **[Watch the repo](https://github.com/JWriter20/CaptchaKraken)** to hear about
these as they ship.

---

## License

Source-available under the **CaptchaKraken Source-Available License v1.0** — see
**[LICENSE](./LICENSE)** (plain-English: **[docs/licensing.md](./docs/licensing.md)**).

- ✅ **Build _with_ it** — scrapers, stealth browsers, data pipelines, QA tooling:
  commercial use is fine when captcha solving is an internal, enabling component.
- ⛔ **Don't sell _the solve_** — no captcha-solving-as-a-service, no thin wrappers
  (browser extensions, hosted endpoints, CLIs) whose main purpose is solving, and
  no relaying the model's outputs through a paid solving API.

For otherwise-prohibited uses, open an issue about a commercial license.

---

## Contributing

PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). CI runs a hermetic
no-regression suite (grid detection, the freshness check, and the fetch command)
plus a TypeScript build on every PR — no GPU or network. Release notes:
[CHANGELOG.md](CHANGELOG.md).

---

> ⚠️ Use responsibly and lawfully — respect the terms of service of any site you
> interact with. This project is for legitimate automation, research, and testing.
