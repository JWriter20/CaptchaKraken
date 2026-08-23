<p align="center">
  <img src="https://raw.githubusercontent.com/JWriter20/CaptchaKraken/main/docs/assets/logo-card.png" alt="CaptchaKraken" width="128" height="128">
</p>

<h1 align="center">captchakraken</h1>

<p align="center">
  <b>A captcha solver for browser automation.</b><br>
  The TypeScript driver for <a href="https://github.com/JWriter20/CaptchaKraken">CaptchaKraken</a>.
</p>

Hand it a Playwright or Puppeteer `Page`. It finds the captcha, reads the whole
puzzle with a fine-tuned **Qwen3.5-9B** vision model, and clicks, drags, slides
or types human-like through to a token.

Run the model on **your own hardware**, or point it at the **hosted API** and run
nothing at all.

> Full docs — demo videos, accuracy, self-hosting — live in the main repo
> **[CaptchaKraken](https://github.com/JWriter20/CaptchaKraken)**.

## Install

```bash
npm install captchakraken
```

The package bundles the Python engine (`captchakraken`) and, on `postinstall`,
creates a local venv with its lightweight core deps, so tile detection and the
inference client work out of the box. It ships **no browser** — bring your own
Playwright-compatible launcher (camoufox and patchright both work).

- Skip the Python bootstrap: `CAPTCHA_KRAKEN_SKIP_PYTHON_SETUP=1`
- To **self-host** the model, run the repo's `setup.sh` (installs vLLM + weights).
  Otherwise point `VLLM_BASE_URL` at a server you already run.

## No GPU? Use the hosted API

Point the client at `https://api.captchakraken.com/v1` and run no model at all.
Sign in at [captchakraken.com/signin](https://captchakraken.com/signin) for a
`ck_live_…` key, or let the MCP server write one for you:

```bash
claude mcp add captchakraken -- npx -y captchakraken-mcp
# then call sign_in, then create_api_key
```

`create_api_key` writes the key and the endpoint to `~/.captchakraken/credentials`,
which the client reads on its own — **no environment variables needed**.

## What it solves

| Vendor | Puzzles |
|---|---|
| **reCAPTCHA** | 3×3 and 4×4 image grids, including the dynamic re-deal |
| **hCaptcha** | Image grids, click, drag, connect-the-path, tetris-fit, animated |
| **GeeTest** v3 + v4 | Slide, icon, nine, svg, gobang, iconcrush |
| **NetEase Yidun** | Jigsaw, picture-click, icon-click |
| **Tencent, Lemin, Prosopo** | Slide, cropped-image and grid flows |
| **BotDetect, MTCaptcha, Yandex** | Distorted text — read and typed, not clicked |
| **Cloudflare Turnstile** | Via the checkbox flow (free on the hosted API) |

**44 puzzle types**, driven end to end in CI against generated fixtures on both
the TypeScript and Python ports. Animated challenges are recorded, sliced into
keyframes and answered with the frame the action belongs to.

## Usage

```typescript
import { chromium } from 'playwright';            // or patchright / camoufox-js
import { CaptchaKrakenSolver } from 'captchakraken';

const browser = await chromium.launch({ headless: false });
const page = await (await browser.newContext()).newPage();
await page.goto('https://www.google.com/recaptcha/api2/demo');

// Reads VLLM_BASE_URL + CAPTCHA_KRAKEN_API_KEY from the environment.
const solver = new CaptchaKrakenSolver();
await solver.solve(page);            // detect → read → act → verify

await browser.close();
```

Puppeteer users wrap the page once with `fromPuppeteer(page)`.

## Configuration

| Variable | Meaning |
|---|---|
| `VLLM_BASE_URL` | Inference endpoint (local vLLM, a server you run, or `https://api.captchakraken.com/v1`) |
| `CAPTCHA_KRAKEN_API_KEY` | Bearer token (`VLLM_API_KEY` also accepted) |

Both fall back to `~/.captchakraken/credentials` when unset, so the hosted API
needs no environment variables at all.

## License

**CaptchaKraken Source-Available License v1.1** — see [LICENSE](./LICENSE).
Build *with* it (scrapers, QA tooling) and run it against **any** browser you
like, stealth or not. You may **not sell the solve itself**, ship a thin wrapper
(browser extension, hosted solving API), or **bundle it as a built-in feature of
a stealth/antidetect browser you distribute** — using it with one is fine. Those
three are licensable, not categorically refused: open an issue to ask.
