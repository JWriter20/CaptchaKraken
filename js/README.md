# captchakraken

The TypeScript browser driver for [CaptchaKraken](https://github.com/JWriter20/CaptchaKraken).
Hand it a Playwright/Puppeteer `Page`; it finds the captcha, reads the grid with
a fine-tuned **Qwen3.5-9B** vision model on **vLLM**, clicks human-like, and
verifies through to a token.

> Full docs — demo videos, accuracy, self-hosting — live in the main repo
> **[CaptchaKraken](https://github.com/JWriter20/CaptchaKraken)**.

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
| `VLLM_BASE_URL` | Inference endpoint (local vLLM, or a server you run) |
| `CAPTCHA_KRAKEN_API_KEY` | Bearer token (`VLLM_API_KEY` also accepted) |

## License

**CaptchaKraken Source-Available License v1.0** — see [LICENSE](./LICENSE).
Build *with* it (scrapers, stealth browsers, QA tooling); you may **not sell the
solve itself** or ship a thin wrapper (browser extension, hosted solving API).
