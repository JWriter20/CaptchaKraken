import { test, expect } from '@playwright/test';
import { CaptchaKrakenSolver } from '../src/solver.js';
import * as path from 'path';
import * as dotenv from 'dotenv';
import * as fs from 'fs';
import { Camoufox } from '@jobharvest/camoufox-js';

// Load environment variables
dotenv.config();

// Determine REPO_PATH with fallback to submodule
let REPO_PATH = process.env.CAPTCHA_KRAKEN_REPO_PATH;
if (!REPO_PATH) {
  const submodulePath = path.resolve(__dirname, '..', 'CaptchaKraken-cli');
  if (fs.existsSync(submodulePath) && fs.existsSync(path.join(submodulePath, 'src', 'cli.py'))) {
    REPO_PATH = submodulePath;
  }
}

// Determine PYTHON_COMMAND with fallback to local venv
let PYTHON_COMMAND = process.env.PYTHON_COMMAND;
if (!PYTHON_COMMAND && REPO_PATH) {
  // Check for venv in root
  const rootVenv = path.resolve(__dirname, '..', '.venv', 'bin', 'python');
  if (fs.existsSync(rootVenv)) {
    PYTHON_COMMAND = rootVenv;
  } else {
    // Check for venv in submodule
    const submoduleVenv = path.join(REPO_PATH, '.venv', 'bin', 'python');
    if (fs.existsSync(submoduleVenv)) {
      PYTHON_COMMAND = submoduleVenv;
    }
  }
}
// Final fallback to system python
if (!PYTHON_COMMAND) {
  PYTHON_COMMAND = 'python3';
}

const MODEL = process.env.CAPTCHA_LORA_NAME || 'captcha';
const API_KEY = process.env.VLLM_API_KEY;

// Skip tests if REPO_PATH is not configured
const testWithSolver = test.extend<{ solver: CaptchaKrakenSolver }>({
  browser: [async ({ }, use) => {
    // Linux server flow: "virtual" → camoufox-js spawns its own Xvfb. macOS / local
    // dev with a display: pass CAPTCHA_HEADED=1 to launch headed; otherwise true.
    const headless: boolean | 'virtual' =
      process.platform === 'linux' ? 'virtual' :
      process.env.CAPTCHA_HEADED === '1' ? false : true;
    const browser = await Camoufox({ headless } as any);
    await use(browser);
    await browser.close();
  }, { scope: 'worker' }],

  solver: async ({ }, use) => {
    if (!REPO_PATH || !fs.existsSync(REPO_PATH)) {
      console.warn('Skipping solving tests: CAPTCHA_KRAKEN_REPO_PATH not set or invalid.');
      test.skip();
    }

    const solver = new CaptchaKrakenSolver({
      repoPath: REPO_PATH!,
      pythonCommand: PYTHON_COMMAND,
      model: MODEL,
      apiKey: API_KEY,
      // Recaptcha can throw 3-4 puzzles in a row before passing, and 3x3
      // dynamic puzzles refresh tiles after each click. Each refresh ~ one
      // new puzzle = one new chance. 25 loops gives us enough budget while
      // keeping the test under 4 minutes.
      maxSolveLoops: 25,
      // 3x3 dynamic tiles fade in over ~1.5s after a click. Screenshotting
      // before they're fully drawn yields washed-out images. Bump to 3s.
      postSolveDelayMs: 3000,
      overallSolveTimeoutMs: 240_000,
    });

    await use(solver);
  }
});

testWithSolver.describe('Real World Solving Tests', () => {
  // Increase timeout for real solving (AI models can be slow)
  testWithSolver.slow();

  testWithSolver('Recaptcha (Google Demo) - Solve', async ({ page, solver }) => {
    await page.goto('https://www.google.com/recaptcha/api2/demo');

    await solver.solve(page as any);

    // Signal 1: the anchor iframe's checkbox gains the `-checked` class.
    // (`-checkmark` is the green check graphic and is always in the DOM —
    // do not check that one; check the parent state class.)
    const anchorFrame = page.frames().find(f => f.url().includes('recaptcha/api2/anchor'));
    const isChecked = await anchorFrame?.locator('.recaptcha-checkbox-checked').count() ?? 0;

    // Signal 2: the hidden `g-recaptcha-response` textarea on the main page
    // is filled with the JWT recaptcha would post to a real backend. This is
    // the canonical server-side success indicator.
    const token = await page.$eval(
      'textarea#g-recaptcha-response, textarea[name="g-recaptcha-response"]',
      el => (el as HTMLTextAreaElement).value,
    ).catch(() => '');

    expect(isChecked, 'anchor checkbox should have .recaptcha-checkbox-checked').toBeGreaterThan(0);
    expect(token, 'g-recaptcha-response textarea should hold a token').toBeTruthy();
    expect(token.length, 'token should be non-trivial length').toBeGreaterThan(20);
  });

  testWithSolver('hCaptcha (Demo) - Solve', async ({ page, solver }) => {
    // accounts.hcaptcha.com/demo uses a high-difficulty sitekey; the
    // democaptcha mirror uses the always-pass sitekey 10000000-ffff-...,
    // which actually validates real model output. Toggle via env if needed.
    const url = process.env.HCAPTCHA_DEMO_URL
      ?? 'https://accounts.hcaptcha.com/demo';
    await page.goto(url);

    await solver.solve(page as any);

    // hCaptcha's success token. Server-side validation only accepts non-empty
    // JWTs; a stale/empty value here means the captcha was not actually solved.
    const response = await page.$eval(
      '[name="h-captcha-response"]',
      el => (el as HTMLTextAreaElement).value,
    ).catch(() => '');
    expect(response, 'h-captcha-response token').toBeTruthy();
    expect(response.length).toBeGreaterThan(20);
  });

  testWithSolver('Cloudflare Turnstile (2Captcha Demo) - Solve', async ({ page, solver }) => {
    await page.goto('https://2captcha.com/demo/cloudflare-turnstile');

    // Turnstile often solves automatically, but sometimes requires a click.
    // solver.solve will find the widget and click if needed (if it identifies a click target).
    // Note: Cloudflare usually doesn't have "click targets" inside the widget in the same way,
    // it's just one big button or auto.
    // The CLI needs to support identifying the "Verify you are human" checkbox area.

    await solver.solve(page as any);

    // Wait for success
    // On this demo page, the success is often indicated by the token input being filled
    // or the widget state changing.

    // Poll for token
    await expect(async () => {
      const val = await page.$eval('[name="cf-turnstile-response"]', el => (el as HTMLInputElement).value);
      expect(val).not.toContain('DUMMY'); // The initial value is DUMMY TOKEN in the example HTML sometimes?
      // Actually the example in prompt had "XXXX.DUMMY.TOKEN.XXXX".
      expect(val).not.toContain('DUMMY.TOKEN');
      expect(val).toBeTruthy();
    }).toPass({ timeout: 15000 });
  });
});

