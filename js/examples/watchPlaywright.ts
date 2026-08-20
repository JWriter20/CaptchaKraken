/**
 * The auto-solver: install once, and captchas are solved as they appear.
 *
 * `solve(page)` handles whatever is on the page right now. `watch(page)` is for
 * when you do not know WHERE in a script a challenge will interrupt you — it
 * returns immediately and works in the background under your automation.
 *
 * It injects nothing into the page on any launcher. Under camoufox its DOM
 * reads run in the isolated Juggler world by default, because that is
 * camoufox's default for all Playwright evaluation.
 *
 *   npm i -D playwright tsx && npx playwright install chromium
 *   npx tsx examples/watchPlaywright.ts [url] [seconds]
 */
import { chromium } from 'playwright';
import { CaptchaKrakenSolver } from 'captchakraken';

async function main() {
  const url = process.argv[2] ?? 'https://www.google.com/recaptcha/api2/demo';
  const seconds = Number(process.argv[3] ?? 30);

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  // ── install once, before the navigation that might raise a challenge ─────
  const solver = new CaptchaKrakenSolver();
  const watcher = solver.watch(page, {
    onSolved: (r) => console.log('✅ solved one:', r.isSolved),
    onError: (e) => console.warn('solve failed:', (e as Error).message),
  });
  // ─────────────────────────────────────────────────────────────────────────

  await page.goto(url);
  await page.waitForTimeout(seconds * 1000);   // your automation runs here

  await watcher.stop();                        // waits for a solve in flight
  console.log(`stopped after ${watcher.solves} solve(s)`);
  await browser.close();
}

main().catch((e) => { console.error(e); process.exit(1); });
