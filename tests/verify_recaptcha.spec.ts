import { test, expect } from '@playwright/test';
import { CaptchaKrakenSolver } from '../src/solver.js';
import * as path from 'path';
import * as dotenv from 'dotenv';
import * as fs from 'fs';
import { Camoufox } from '@jobharvest/camoufox-js';

dotenv.config();

const testWithSolver = test.extend<{ solver: CaptchaKrakenSolver }>({
  browser: [async ({ }, use) => {
    // Launch headless: true for linux server env
    const browser = await Camoufox({ headless: true });
    await use(browser);
    await browser.close();
  }, { scope: 'worker' }],

  solver: async ({ }, use) => {
    const cliRoot = path.resolve(__dirname, '..', 'CaptchaKraken-cli');
    
    const solver = new CaptchaKrakenSolver({
      repoPath: cliRoot,
      pythonCommand: 'python3', 
      model: 'Qwen/Qwen3.5-9B', // Passed to server, which maps to lora/backend
      apiProvider: 'local-server' as any,
      serverUrl: 'http://localhost:8001',
      maxSolveLoops: 10,
      postSolveDelayMs: 2000,
      overallSolveTimeoutMs: 120_000
    });

    await use(solver);
  }
});

testWithSolver('Recaptcha (Google Demo) - Solve', async ({ page, solver }) => {
  console.log('Navigating to Google ReCAPTCHA Demo...');
  await page.goto('https://google.com/recaptcha/api2/demo');

  // Attempt to solve
  console.log('Starting solver...');
  await solver.solve(page as any);

  // Verification
  console.log('Verifying solution...');
  const anchorFrame = page.frames().find(f => f.url().includes('recaptcha/api2/anchor'));
  const isChecked = await anchorFrame?.locator('.recaptcha-checkbox-checked').count();
  
  if (isChecked && isChecked > 0) {
    console.log('SUCCESS: ReCAPTCHA checked!');
  } else {
    console.log('FAILURE: ReCAPTCHA not checked.');
  }
  
  expect(isChecked).toBeGreaterThan(0);
  
  // Wait a bit to ensure video captures the success state
  await page.waitForTimeout(3000);
});
