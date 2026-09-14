import { chromium } from 'playwright';
import { CaptchaKrakenSolver } from 'captchakraken';

async function main() {
  const url = process.argv[2] ?? 'https://www.google.com/recaptcha/api2/demo';
  const seconds = Number(process.argv[3] ?? 30);

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  const solver = new CaptchaKrakenSolver();
  const watcher = solver.watch(page, {
    onSolved: (r) => console.log('✅ solved one:', r.isSolved),
    onError: (e) => console.warn('solve failed:', (e as Error).message),
  });

  await page.goto(url);
  await page.waitForTimeout(seconds * 1000);

  await watcher.stop();
  console.log(`stopped after ${watcher.solves} solve(s)`);
  await browser.close();
}

main().catch((e) => { console.error(e); process.exit(1); });
