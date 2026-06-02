import { test, expect } from '@playwright/test';
import { CaptchaKrakenSolver } from '../src/solver.js';
import * as path from 'path';
import * as dotenv from 'dotenv';
import * as fs from 'fs';
import { Camoufox } from '@jobharvest/camoufox-js';

// Load environment variables from the project root .env
dotenv.config({ path: path.resolve(__dirname, '..', '..', '.env') });
dotenv.config(); // Also load local .env if present

const REPO_PATH = path.resolve(__dirname, '..', 'CaptchaKraken-cli');
const CLI_VENV = path.join(REPO_PATH, '.venv', 'bin', 'python');
const PYTHON_COMMAND = fs.existsSync(CLI_VENV) ? CLI_VENV : 'python3';

const RECAPTCHA_URL = 'https://nopecha.com/captcha/recaptcha#moderate';
const NUM_SOLVES = parseInt(process.env.NUM_SOLVES || '10', 10);

const testWithSolver = test.extend<{ solver: CaptchaKrakenSolver }>({
  browser: [async ({}, use) => {
    const browser = await Camoufox({ headless: true });
    await use(browser);
    await browser.close();
  }, { scope: 'worker' }],

  solver: async ({}, use) => {
    if (!fs.existsSync(REPO_PATH)) {
      console.warn(`Skipping: CaptchaKraken-cli not found at ${REPO_PATH}`);
      test.skip();
    }

    const solver = new CaptchaKrakenSolver({
      repoPath: REPO_PATH,
      pythonCommand: PYTHON_COMMAND,
      model: process.env.CAPTCHA_LORA_NAME || 'captcha-grid',
      apiProvider: 'transformers',
      maxSolveLoops: 15,
      postSolveDelayMs: 2500,
      overallSolveTimeoutMs: 300_000, // 5 min per solve (model loading is slow first time)
    });

    await use(solver);
  }
});

testWithSolver.describe('LoRA Recaptcha Solving - 10 Attempts', () => {
  // Each test gets a generous timeout since transformers inference is slower
  testWithSolver.setTimeout(600_000); // 10 min per individual test

  const results: { attempt: number; solved: boolean; error?: string; durationMs: number }[] = [];

  for (let i = 1; i <= NUM_SOLVES; i++) {
    testWithSolver(`Recaptcha Solve #${i}`, async ({ page, solver }) => {
      const start = Date.now();

      await page.goto(RECAPTCHA_URL, { waitUntil: 'networkidle' });
      // Small wait for recaptcha to fully initialize
      await page.waitForTimeout(2000);

      let solved = false;
      let error: string | undefined;

      try {
        await solver.solve(page as any);

        // Check if the recaptcha checkbox is checked
        const anchorFrame = page.frames().find(f => f.url().includes('recaptcha/api2/anchor'));
        const isChecked = await anchorFrame?.locator('.recaptcha-checkbox-checked').count();
        solved = (isChecked ?? 0) > 0;
      } catch (e: any) {
        error = e.message;
        console.error(`Attempt #${i} failed: ${e.message}`);
      }

      const durationMs = Date.now() - start;
      results.push({ attempt: i, solved, error, durationMs });

      console.log(`\n=== Attempt #${i}: ${solved ? 'SOLVED' : 'FAILED'} (${(durationMs / 1000).toFixed(1)}s) ===`);

      if (i === NUM_SOLVES) {
        // Print final summary after last test
        const solvedCount = results.filter(r => r.solved).length;
        const avgTime = results.reduce((s, r) => s + r.durationMs, 0) / results.length / 1000;
        console.log('\n========================================');
        console.log(`  FINAL RESULTS: ${solvedCount}/${NUM_SOLVES} solved`);
        console.log(`  Success rate: ${((solvedCount / NUM_SOLVES) * 100).toFixed(0)}%`);
        console.log(`  Avg time: ${avgTime.toFixed(1)}s`);
        console.log('========================================\n');

        for (const r of results) {
          const status = r.solved ? 'PASS' : 'FAIL';
          const errMsg = r.error ? ` - ${r.error.slice(0, 80)}` : '';
          console.log(`  #${r.attempt}: ${status} (${(r.durationMs / 1000).toFixed(1)}s)${errMsg}`);
        }
      }

      expect(solved, `Recaptcha solve #${i} should succeed`).toBe(true);
    });
  }
});
