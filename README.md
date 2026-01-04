# PlaywrightCaptchaKrakenJS

> ⭐ If this captcha solver was useful to you, please leave a star on [GitHub](https://github.com/JWriter20/PlaywrightCaptchaKrakenJS)!

A Patchright (Playwright) wrapper for [CaptchaKraken-cli](https://github.com/JWriter20/CaptchaKraken-cli) to solve captchas (Recaptcha, hCaptcha, Cloudflare Turnstile) using AI vision models.

## Current Capabilities

Right now, we can reliably solve:
- **Checkbox captchas**: ~100% success rate
- **Image captchas**: ~60% success rate (work in progress with finetuning vision models to improve this)

Other kinds of captchas have not really been tested. Development will primarily focus on reCAPTCHA, Cloudflare Turnstile, and hCaptcha.

## Prerequisites

1.  **Node.js** and **npm**.
2.  **Python 3.10+** installed.
3.  **CUDA-capable GPU** (recommended for local `vllm` or `transformers` inference).

## Installation

```bash
npm install playwright-captcha-kraken-js patchright-core
```

If you're cloning this repository, initialize the git submodule:

```bash
git submodule update --init --recursive
```

On install, this package will automatically create a local venv at `CaptchaKraken-cli/.venv` and install
Python dependencies via an `npm postinstall` hook.

- **Skip python setup**: set `CAPTCHA_KRAKEN_SKIP_PYTHON_SETUP=1`
- **Use a specific python**: set `CAPTCHA_KRAKEN_PYTHON=/path/to/python3`

## Usage

```typescript
import { chromium } from 'patchright';
import { CaptchaKrakenSolver } from 'playwright-captcha-kraken-js';

(async () => {
  const browser = await chromium.launch({ headless: false });
  const page = await browser.newPage();
  
  // Configure the solver
  const solver = new CaptchaKrakenSolver({
    apiProvider: 'vllm', // or 'transformers'
    // model: 'Jake-Writer-Jobharvest/qwen3-vl-8b-merged-bf16', // Default for vllm
  });

  await page.goto('https://www.google.com/recaptcha/api2/demo');

  // Attempt to solve the captcha
  await solver.solve(page);

  await browser.close();
})();
```

## Configuration

| Option | Type | Default | Description |
|---|---|---|---|
| `repoPath` | `string` | *(auto)* | Path to the bundled `CaptchaKraken-cli` directory (usually not needed). |
| `pythonCommand` | `string` | *(auto)* | Python command to use. Usually not needed - automatically uses the venv python created during installation. |
| `model` | `string` | *(auto)* | The vision model to use. |
| `apiProvider` | `'vllm' \| 'transformers'` | `'vllm'` | The backend provider. |
| `maxSolveLoops` | `number` | `10` | Max number of detect→solve iterations in a single `solve()` call. |
| `postSolveDelayMs` | `number` | `1200` | Delay after each iteration before re-detecting. |
| `overallSolveTimeoutMs` | `number` | `120000` | Overall time limit for the whole `solve()` call. |

## Testing

To run the tests:

```bash
npm test
```

### End-to-End Solving Tests

To run the real-world solving tests (which connect to your local `CaptchaKraken-cli`):

```bash
API_PROVIDER="vllm" \
npx playwright test tests/solving.spec.ts
```

Note: The tests will automatically use `./CaptchaKraken-cli` as the default path. You can override it with `CAPTCHA_KRAKEN_REPO_PATH` if needed.
