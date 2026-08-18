/**
 * Vanilla Playwright + CaptchaKraken.
 *
 * Playwright needs NO adapter. The solver types `solve(page)` against a
 * structural Playwright `Page` this package defines itself, so a real
 * Playwright page satisfies it as-is — as do patchright's and camoufox-js's.
 * The package depends on no browser library at all; you bring your own.
 *
 *   npm i -D playwright tsx && npx playwright install chromium
 *   export VLLM_BASE_URL=https://api.captchakraken.com/v1
 *   export CAPTCHA_KRAKEN_API_KEY=ck_live_...
 *   npx tsx examples/withPlaywright.ts [url]
 *
 * `main()` rather than top-level await on purpose: this package is CommonJS,
 * and tsx transpiles a .ts file here to CJS, where top-level await is a syntax
 * error. An async main is the shape that runs unchanged in either module system.
 */
import { chromium } from 'playwright';
import { CaptchaKrakenSolver } from 'captchakraken';

async function main() {
  const url = process.argv[2] ?? 'https://www.google.com/recaptcha/api2/demo';

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto(url);

  // ── the whole integration: construct, solve ──────────────────────────────
  const solver = new CaptchaKrakenSolver();
  const result = await solver.solve(page);
  // ─────────────────────────────────────────────────────────────────────────

  console.log(result?.isSolved ? '✅ solved' : '❌ not solved');
  await browser.close();
}

main().catch((e) => { console.error(e); process.exit(1); });
