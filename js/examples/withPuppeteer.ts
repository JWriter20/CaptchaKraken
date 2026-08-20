/**
 * Puppeteer + CaptchaKraken.
 *
 * Puppeteer's `Page` is ~95% Playwright's, differing in a handful of method
 * names — `viewport()` not `viewportSize()`, `{visible}` not `{state}`, no
 * `waitForTimeout`, no combo syntax in `keyboard.press`. `fromPuppeteer(page)`
 * bridges exactly those, so the solver stays coupled to neither library.
 *
 * That wrapper is checked against the real library in
 * `src/browser-compat.test.ts`, not only against a mock.
 *
 *   npm i -D puppeteer tsx
 *   export VLLM_BASE_URL=https://api.captchakraken.com/v1
 *   export CAPTCHA_KRAKEN_API_KEY=ck_live_...
 *   npx tsx examples/withPuppeteer.ts [url]
 */
import puppeteer from 'puppeteer';
import { CaptchaKrakenSolver, fromPuppeteer } from 'captchakraken';

async function main() {
  const url = process.argv[2] ?? 'https://www.google.com/recaptcha/api2/demo';

  const browser = await puppeteer.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto(url);

  // ── the whole integration: construct, wrap, solve ────────────────────────
  const solver = new CaptchaKrakenSolver();
  const result = await solver.solve(fromPuppeteer(page));
  // ─────────────────────────────────────────────────────────────────────────

  console.log(result?.isSolved ? '✅ solved' : '❌ not solved');
  await browser.close();
}

main().catch((e) => { console.error(e); process.exit(1); });
