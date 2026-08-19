# 🐙 CaptchaKraken — Documentation

Everything beyond the [project overview](../README.md), organized into focused
guides. Start here.

> One repo, two published ports — the TypeScript browser driver
> (npm: `captchakraken`) and the Python engine (PyPI: `captchakraken`). ⭐ **Star
> & watch** [the repo](https://github.com/JWriter20/CaptchaKraken) for smaller
> models, video support, and new puzzle types.

## 📚 Guides

| Guide | What's inside |
|---|---|
| [☁️ Hosted API](./hosted-api.md) | Sign in, keys, pricing, per-session billing, and every error code — the no-GPU path. |
| [🖥️ Self-hosting](./self-hosting.md) | One-command install, model-size gate, server management, configuration, and **updating** (`captchakraken fetch`). |
| [🧑‍💻 Usage](./usage.md) | Install both ports, the Python one-liner, all four browser frameworks (Playwright / Patchright / camoufox-js / Puppeteer), and migrating from v1. |
| [⚙️ How it works](./how-it-works.md) | The detect → grid → click → verify pipeline, the OpenCV grid tracer, the stale-frame freshness guard, and solution dedup. |
| [📊 Performance](./performance.md) | Model accuracy, the memory-bandwidth speed model + per-device throughput, and IP-reputation guidance. |
| [🗺️ Roadmap](./roadmap.md) | What shipped recently, what's in progress, and what's planned (video support, more captcha types). |
| [⚖️ Licensing](./licensing.md) | Plain-English explainer of the source-available license — what you may and may not build. |

## ✅ What it solves today

CaptchaKraken detects the captcha, solves it, clicks, and verifies.

| Captcha type | Status |
|---|---|
| ✅ Checkbox / "I'm not a robot" | Works end-to-end |
| ✅ **reCAPTCHA 3×3** (dynamic) | Works end-to-end |
| ✅ **reCAPTCHA 4×4** (one-shot) | Works end-to-end |
| ✅ **hCaptcha 3×3 image grid** | Works end-to-end |
| ✅ **hCaptcha click / drag puzzles** | Full-puzzle model → pixel click/drag actions |
| ✅ Cloudflare Turnstile | Works via the checkbox flow |
| 🟡 **Animated / video challenges** | Driver support ships: recorded, cut into keyframes, solved as a multi-image prompt, then clicked once the widget returns to the chosen frame. Accuracy depends on the adapter; the one `setup.sh` installs skips them ([roadmap](./roadmap.md)) |

Non-grid still-image puzzles — **click** ("click each …"), **drag** ("drag the
piece into place"), path/connect, "choose the card" — route to the full-puzzle
model, which returns pixel-space click/drag actions the browser replays. If the
model can't produce a usable action for a given frame, the solver fails fast
rather than guessing.

---

← Back to the [project overview](../README.md)
