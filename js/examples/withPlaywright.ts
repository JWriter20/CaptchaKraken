import { chromium } from 'playwright';
import { CaptchaKrakenSolver } from 'captchakraken';

async function main() {
  const url = process.argv[2] ?? 'https://www.google.com/recaptcha/api2/demo';

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto(url);

  const solver = new CaptchaKrakenSolver();
  const result = await solver.solve(page);

  console.log(result?.isSolved ? '✅ solved' : '❌ not solved');
  await browser.close();
}

main().catch((e) => { console.error(e); process.exit(1); });
