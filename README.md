<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/logo-dark.svg">
    <img src="./docs/assets/logo-light.svg" alt="CaptchaKraken" width="140" height="140">
  </picture>
</p>

<h1 align="center">CaptchaKraken</h1>

<p align="center">
  <b>A captcha solver for browser automation.</b><br>
  Give it a page — it finds the captcha, reads the puzzle with a fine-tuned
  <b>Qwen3.5-9B</b> vision model, and clicks through to a token.<br>
  Run the model on <b>your own hardware</b>, or call the <b>hosted API</b> and
  run nothing at all.
</p>

<p align="center">
  <a href="https://www.npmjs.com/package/captchakraken"><img src="https://img.shields.io/npm/v/captchakraken?logo=npm&label=npm" alt="npm"></a>
  <a href="https://pypi.org/project/captchakraken/"><img src="https://img.shields.io/pypi/v/captchakraken?logo=pypi&logoColor=white&label=PyPI" alt="PyPI"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-Source--Available-blue" alt="License: Source-Available"></a>
  <a href="https://github.com/JWriter20/CaptchaKraken/actions/workflows/ci.yml"><img src="https://github.com/JWriter20/CaptchaKraken/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

> ⭐ **Enjoying CaptchaKraken?** [Star & watch the repo](https://github.com/JWriter20/CaptchaKraken)
> for new puzzle types, smaller models, and video support. One repo, two
> published ports — the TypeScript browser driver (**npm:** `captchakraken`) and
> the Python engine (**PyPI:** `captchakraken`).

---

## Setup

Two ways to run the model. The client code is identical — only the endpoint
changes, so you can switch later by editing one variable.

| | ☁️ **Hosted API** | 🖥️ **Self-hosted** |
|---|---|---|
| You need | Nothing | 11 GB+ of VRAM or Apple unified memory |
| Takes | ~1 minute | ~20 minutes (model download) |
| Runs | Our production adapter, on our fleet — the **Twilight** weights, served for you | The same model family, on your card |
| Cost | $0.30 per 1,000 image responses. Checkbox and Turnstile are free. | Your electricity |
| Screenshots | Sent to our gateway | Never leave your machine |

**Agents:** the same instructions in a shorter, machine-readable form are in
**[AGENTS.md](./AGENTS.md)**.

### Option 1 — Hosted API

No GPU, no download, no server.

The easiest path is the account MCP server. It signs you in and writes the key
to disk for you, so **you set no environment variables at all**:

```bash
claude mcp add captchakraken -- npx -y captchakraken-mcp
```

Then call `sign_in` (a human approves it in a browser with GitHub — new accounts
get free trial credits), and call `create_api_key`. The key and the endpoint are
written to `~/.captchakraken/credentials`, which the solver reads by itself. The
key is never printed into your terminal or your agent's transcript.

Prefer to do it by hand? Sign in at
**[captchakraken.com/signin](https://captchakraken.com/signin)**, copy your
`ck_live_…` key, and set two variables:

```bash
export VLLM_BASE_URL=https://api.captchakraken.com/v1
export CAPTCHA_KRAKEN_API_KEY=ck_live_your_key_here
```

You are billed per **model response**, not per captcha — one captcha usually
takes 1–2. A single solve attempt is capped at **5 billable responses**, so a
stubborn captcha cannot run up an unbounded bill.

### Option 2 — Self-host

One command. It checks your GPU or Apple memory, picks a model size that fits,
downloads it, and writes a config file:

```bash
git clone https://github.com/JWriter20/CaptchaKraken
cd CaptchaKraken
./setup.sh
source captchakraken.env
```

| Your memory | What it installs |
|---|---|
| **22 GB+** | 8-bit base (~14 GB) — best accuracy |
| **11–22 GB** | 4-bit base (~6 GB) — lighter |
| **Under 11 GB** | Stops and explains your options |

**You never start a server.** vLLM starts by itself on your first solve and
stays up. Hardware notes, server commands, and updating:
**[docs/self-hosting.md](./docs/self-hosting.md)**.

> `setup.sh` installs a **LoRA adapter**, which needs vLLM. If you use another
> runtime — or just want one file and no adapter flags — serve the merged
> **Sunlight** (4-bit) or **Twilight** (8-bit) builds instead. Both are public.
> See [The models](#the-models).

**Already run your own vLLM server?** Point at it and skip all of the above. A
non-local URL is never auto-started or managed for you:

```bash
export VLLM_BASE_URL=https://your-server:8000/v1
export CAPTCHA_KRAKEN_API_KEY=your-server-key
```

### Then install the client and solve

Same for every option above. Python needs 3.10+; neither package installs a
browser, so bring your own.

```bash
npm install captchakraken        # TypeScript browser driver
pip install captchakraken        # Python engine + `captchakraken` CLI
```

In the browser — hand it any Playwright-compatible page:

```typescript
import { chromium } from 'playwright';
import { CaptchaKrakenSolver } from 'captchakraken';

const page = await (await (await chromium.launch()).newContext()).newPage();
await page.goto('https://www.google.com/recaptcha/api2/demo');

await new CaptchaKrakenSolver().solve(page);   // detect → solve → click → verify
```

In Python — synchronous Playwright:

```python
from playwright.sync_api import sync_playwright
from captchakraken import PageSolver

with sync_playwright() as p:
    page = p.chromium.launch().new_page()
    page.goto("https://www.google.com/recaptcha/api2/demo")
    print(PageSolver().solve(page).is_solved)
```

Or with no browser at all — a screenshot in, a JSON click plan out:

```bash
captchakraken path/to/captcha.png
```

Check your setup any time with `captchakraken server status`. Every browser
framework (Playwright, Patchright, camoufox, Puppeteer) and the background
watcher are in **[docs/usage.md](./docs/usage.md)**.

**Stay current** — pull the latest model + engine in one step, no reinstall:

```bash
captchakraken fetch
```

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
| ✅ **GeeTest** (v3 + v4) | Slide, icon, nine, svg. `gobang` and `iconcrush` are weak — see below |
| ✅ **NetEase Yidun** | Jigsaw, picture-click, icon-click |
| ✅ **Lemin, Prosopo, Tencent** | Cropped-image, grid, and slide flows |
| ✅ **Distorted text** | BotDetect, MTCaptcha, Yandex — read and typed, not clicked |
| 🟡 Video challenges | Driver support ships; the adapter `setup.sh` installs skips them ([roadmap](./docs/roadmap.md)) |

The non-Google/hCaptcha vendors are driven end-to-end in CI against generated
fixtures in **both** ports. Per-vendor accuracy varies more than the headline
grids do; `geetest_v4_gobang` and `geetest_v4_iconcrush` are the two that
currently fail to drive to completion, and are best treated as experimental.

### Accuracy, measured

> 🔬 **Being re-measured.** The figures previously published here were taken on
> 2026-07-27 under an eval split that let some hand-labeled captures reach
> training, so they measured memorisation as well as skill. That split was
> replaced — **every** real capture is now held out and nothing hand-labeled
> trains — and the numbers are being re-taken against the new held-out set on
> the next full training run. Rather than restate figures we no longer stand
> behind, this section is blank until that lands.

What the measurement will be, so you can hold us to it: exact set match — every
correct tile and no incorrect ones, no partial credit, because a
partially-correct grid answer is a rejected captcha — taken over the full
customer path (HTTPS → gateway → vLLM) rather than a local checkpoint, against
captures the adapter has never trained on.

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
| 🟦 **Sunlight** | 0–200 m | The adapter merged into the base and quantised to **4-bit (AWQ)**. ~9 GB, comfortable on 11 GB of VRAM. | [Public](https://huggingface.co/CaptchaKraken/Sunlight-AWQ-4bit) |
| 🟦 **Twilight** | 200–1,000 m | The same merge at **8-bit (FP8)**. ~14 GB, wants 22 GB to serve comfortably. | [Public](https://huggingface.co/CaptchaKraken/Twilight-FP8) |
| ⬛ **Abyss** | 4,000–6,000 m | **In training, not serving yet.** Trained against the failures of the open weights. Hosted-only when it lands. | **Never published** |

Alongside them is the **LoRA adapter**, which is what `setup.sh` installs:
[`CaptchaKraken/CaptchaKraken-Lora-v1.2`](https://huggingface.co/CaptchaKraken/CaptchaKraken-Lora-v1.2).
It is applied at serve time on top of a stock `Qwen3.5-9B-VL`, which means two
downloads and an `--enable-lora` flag — and it is the **newest and strongest**
open weights we publish.

**Sunlight and Twilight exist so you do not have to do that.** They are the
adapter already merged into the base and quantised, so what you download is one
self-contained model that vLLM, and any runtime that loads standard
safetensors, will serve without knowing what a LoRA is.

> ℹ️ **Sunlight and Twilight are a generation behind.** They merge the earlier
> `CaptchaKrakenV1_Lora` (prompt generation 1). The `-Lora-v1.2` adapter above
> is generation 2 and scores higher. Convenience or accuracy — pick one; both
> are supported.

**Abyss is not serving yet.** The hosted API answers with the production
adapter today — the same weights published as **Twilight**. Abyss is the next
model, and it is hosted-only on purpose: it is not a bigger quantisation of the
public weights. Every puzzle the open model gets wrong on the held-out set is
a labelled example of a weakness, and Abyss is trained specifically to close
them — starting with the non-grid hCaptcha puzzles and video, which the open
weights skip rather than guess at. Keeping it on our own fleet is what lets it
keep learning from production failures without shipping a customer's puzzle set
to everyone who runs `hf download`.

Which one you want:

| If | Use |
|---|---|
| You have no GPU | the [hosted API](./docs/hosted-api.md) |
| You want the best open weights, and run vLLM | the **LoRA adapter** — `./setup.sh` |
| You want one file and the simplest serve, 22 GB+ | **Twilight** |
| You want one file and the simplest serve, 11–22 GB | **Sunlight** |
| You don't use vLLM | **Sunlight** or **Twilight** |
| You need video challenges | the [hosted API](./docs/hosted-api.md) — `setup.sh`'s adapter skips them rather than guess |

Serving details for every option: **[docs/self-hosting.md](./docs/self-hosting.md)**.

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

Reproduce it yourself — the demos in this repo drive a real browser end to end:

```bash
cd js
npm install && npm i -D camoufox-js tsx   # example-only deps
npx camoufox-js fetch                     # one-time browser download
source ../captchakraken.env               # written by ./setup.sh

npx tsx examples/demoRecaptcha.ts                      # the demo page above
npx tsx examples/demoRecaptcha.ts https://your.site/   # or your own page
```

Add `--headed` to watch it happen in a visible window. The Python engine ships
the same two demos — `cd python && python examples/demoRecaptcha.py` — and both
take the same arguments.

---

## Documentation

Most of the detail lives in the docs hub — start at **[docs/](./docs/README.md)**.

| Guide | What's in it |
|---|---|
| ☁️ [Hosted API](./docs/hosted-api.md) | Sign in, keys, pricing, per-session billing, error codes |
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
- 🟢 **Shipped** — the **Sunlight** and **Twilight** merges, now public on
  [HuggingFace](https://huggingface.co/CaptchaKraken).
- 🟡 **In progress** — 🎥 **video challenges**, and **Abyss**, the next hosted
  model.
- ⚪ **Planned** — 🧩 more non-grid hCaptcha puzzles (drag, path, tetris-fit).

The visual, always-current version is in **[docs/roadmap.md](./docs/roadmap.md)**.
📣 **[Watch the repo](https://github.com/JWriter20/CaptchaKraken)** to hear about
these as they ship.

---

## License

Source-available under the **CaptchaKraken Source-Available License v1.1** — see
**[LICENSE](./LICENSE)** (plain-English: **[docs/licensing.md](./docs/licensing.md)**).

- ✅ **Build _with_ it** — scrapers, data pipelines, QA tooling: commercial use is
  fine when captcha solving is an internal, enabling component.
- ✅ **Use it with any browser you like** — Camoufox, Puppeteer, Playwright, any
  stealth browser. Running the solver against your own automation is unrestricted.
- ⛔ **Don't sell _the solve_** — no captcha-solving-as-a-service, no thin wrappers
  (browser extensions, hosted endpoints, CLIs) whose main purpose is solving, and
  no relaying the model's outputs through a paid solving API.
- ⛔ **Don't _ship_ the solve** — you may not bundle, preinstall, or advertise this
  as a built-in captcha feature of a stealth browser, antidetect/profile manager,
  or automation platform you distribute to other people. Using it with one is
  fine; shipping it as part of one needs a license.

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
