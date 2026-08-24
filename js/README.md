# captchakraken

The TypeScript browser driver for [CaptchaKraken](https://github.com/JWriter20/CaptchaKraken).
Hand it a Playwright/Puppeteer `Page`; it finds the captcha, reads the grid with
a fine-tuned **Qwen3.5-9B** vision model on **vLLM**, clicks human-like, and
verifies through to a token.

> Full docs — demo videos, accuracy, self-hosting — live in the main repo
> **[CaptchaKraken](https://github.com/JWriter20/CaptchaKraken)**.

## Watch it work

<p align="center">
  <img src="https://raw.githubusercontent.com/JWriter20/CaptchaKraken/main/docs/assets/demo/hcaptcha_grid.webp" width="260"
       alt="A live hCaptcha image select challenge being solved end to end">
  <img src="https://raw.githubusercontent.com/JWriter20/CaptchaKraken/main/docs/assets/demo/recaptcha_4x4.webp" width="260"
       alt="A live reCAPTCHA 4×4 tile grid challenge being solved end to end">
  <img src="https://raw.githubusercontent.com/JWriter20/CaptchaKraken/main/docs/assets/demo/geetest_slide.webp" width="260"
       alt="A live GeeTest slide jigsaw challenge being solved end to end">
</p>

hCaptcha image select 12/12 in 10.5s · reCAPTCHA 4×4 tile grid 9/10 in 8.7s · GeeTest slide jigsaw 10/10 in 7.6s — median of the solved attempts, measured 2026-08-19 on **captcha-v12**
against each vendor's own public demo page. Counts rather than percentages
because ten attempts is not a percentage. Idle time is cut from the clips, so
they run shorter than the solves they show.

**Ten more puzzle types**, as video and with the full method, at
[captchakraken.com](https://captchakraken.com) and in the
[main repo](https://github.com/JWriter20/CaptchaKraken#watch-it-work).

## Install

```bash
npm install captchakraken
```

The package bundles the Python engine (`captchakraken`) and, on `postinstall`,
creates a local venv with its lightweight core deps so grid detection + the vLLM
planner work out of the box. It ships **no browser** — bring your own
Playwright-compatible launcher.

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

## Usage

```typescript
import { chromium } from 'playwright';            // or patchright / camoufox-js
import { CaptchaKrakenSolver } from 'captchakraken';

const browser = await chromium.launch({ headless: false });
const page = await (await browser.newContext()).newPage();
await page.goto('https://www.google.com/recaptcha/api2/demo');

// Reads VLLM_BASE_URL + CAPTCHA_KRAKEN_API_KEY from the environment.
const solver = new CaptchaKrakenSolver();
await solver.solve(page);            // detect → solve grid → click → verify

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
