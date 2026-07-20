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
| ⛔ Video challenges | Skipped for now ([on the roadmap](./docs/roadmap.md)) |

On our hand-labeled real-captcha set the model picks the exactly-correct tiles
**94.7%** of the time for reCAPTCHA 3×3 (**85.8%** overall).
<!-- TODO: re-confirm accuracy on the latest LoRA -->
Full accuracy + speed tables: **[docs/performance.md](./docs/performance.md)**.

---

## Demos

Live solves, recorded straight from the browser:

<!-- BEGIN DEMOS -->
**reCAPTCHA 3×3 — fast solve**

https://github.com/user-attachments/assets/e5e63787-1e5f-46e6-aa7e-f74af9962233

**reCAPTCHA 4×4 — one-shot "select all"**

https://github.com/user-attachments/assets/93b5dd43-c634-4644-8754-fb5f8ab8b9c9

**hCaptcha 3×3 grid — property puzzle**

https://github.com/user-attachments/assets/7df62269-d4fd-4ec8-9562-883409679ab6
<!-- END DEMOS -->

---

## Quickstart

**1. Self-host the model** (one command — detects your GPU/Apple-silicon memory,
picks a model size that fits, downloads it, and writes a config file):

```bash
./setup.sh
```

> 💡 **No GPU?** A hosted cloud API (no model to run) is coming —
> [star the repo](https://github.com/JWriter20/CaptchaKraken) to be notified.
> Full hardware guide: **[docs/self-hosting.md](./docs/self-hosting.md)**.

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

- 🟢 **Recently shipped** — mid-inference **freshness guard** (never act on a
  stale frame) and a one-command **`captchakraken fetch`** updater.
- 🟡 **In progress** — 🎥 **video challenge support**, hosted cloud API.
- ⚪ **Planned** — 🧩 more non-grid hCaptcha puzzles (drag, path, tetris-fit),
  smaller/faster quants, reCAPTCHA 4×4 robustness.

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
