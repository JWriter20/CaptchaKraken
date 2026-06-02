import { test, expect } from '@playwright/test';
import { CaptchaKrakenSolver } from '../src/solver';

test.describe('Local Server Solving Tests', () => {
  let solver: CaptchaKrakenSolver;

  test.beforeEach(async () => {
    solver = new CaptchaKrakenSolver({
      apiProvider: 'local-server',
      serverUrl: 'http://localhost:8000',
      maxSolveLoops: 15,
      postSolveDelayMs: 2000,
      overallSolveTimeoutMs: 300_000
    });
  });

  test('Recaptcha (Google Demo) - Solve via Local Server', async ({ page }) => {
    // Headless mode is already default in Playwright test unless configured otherwise,
    // but the error message suggests it was trying to launch headed or Ozone failed.
    
    // Increase timeout for model loading and solving
    test.slow();
    
    await page.goto('https://google.com/recaptcha/api2/demo');

    // Attempt to solve
    await solver.solve(page as any);

    // Verification
    const anchorFrame = page.frames().find(f => f.url().includes('recaptcha/api2/anchor'));
    const isChecked = await anchorFrame?.locator('.recaptcha-checkbox-checked').count();
    expect(isChecked).toBeGreaterThan(0);
  });

  test('hCaptcha (Demo) - Solve via Local Server', async ({ page }) => {
    // Increase timeout for model loading and solving
    test.slow();
    
    await page.goto('https://accounts.hcaptcha.com/demo');

    // Attempt to solve
    await solver.solve(page as any);

    const response = await page.$eval('[name="h-captcha-response"]', el => (el as HTMLTextAreaElement).value);
    expect(response).toBeTruthy();
  });
});
