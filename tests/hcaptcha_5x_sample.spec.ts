import { test } from '@playwright/test';
import { CaptchaKrakenSolver } from '../src/solver.js';
import * as path from 'path';
import * as dotenv from 'dotenv';
import * as fs from 'fs';
import { Camoufox } from '@jobharvest/camoufox-js';

dotenv.config();
dotenv.config({ path: path.resolve(__dirname, '..', '..', '.env') });

const REPO_PATH = process.env.CAPTCHA_KRAKEN_REPO_PATH
  ?? path.resolve(__dirname, '..', 'CaptchaKraken-cli');
const PYTHON_COMMAND = process.env.PYTHON_COMMAND
  ?? path.join(REPO_PATH, '.venv', 'bin', 'python');
const MODEL = process.env.CAPTCHA_LORA_NAME || 'captcha';
const API_KEY = process.env.CAPTCHA_KRAKEN_API_KEY || process.env.VLLM_API_KEY;
const HCAPTCHA_URL = process.env.HCAPTCHA_DEMO_URL ?? 'https://accounts.hcaptcha.com/demo';
const N = parseInt(process.env.SAMPLE_N || '5', 10);

const SUMMARY_PATH = path.resolve(__dirname, '..', 'hcaptcha_5x_summary.json');

test.describe.configure({ mode: 'serial' });

interface Outcome {
  i: number;
  solved: boolean;
  token: string;
  tokenLen: number;
  elapsedMs: number;
  finalScreenshot: string;
  error?: string;
}
const results: Outcome[] = [];

test.afterAll(() => {
  fs.writeFileSync(SUMMARY_PATH, JSON.stringify(results, null, 2));
  console.log('\n=== hCaptcha 5-sample summary ===');
  for (const r of results) {
    console.log(
      `  #${r.i}: ${r.solved ? 'PASS' : 'FAIL'} ` +
      `(token len ${r.tokenLen}, ${(r.elapsedMs / 1000).toFixed(1)}s) ` +
      `→ ${r.finalScreenshot}` +
      (r.error ? `   error: ${r.error}` : '')
    );
  }
  const passed = results.filter(r => r.solved).length;
  console.log(`Pass rate: ${passed}/${results.length}`);
  console.log(`Summary written to ${SUMMARY_PATH}`);
});

for (let i = 1; i <= N; i++) {
  test(`hCaptcha attempt ${i}`, async () => {
    test.slow();
    const headless: boolean | 'virtual' =
      process.platform === 'linux' ? 'virtual' :
      process.env.CAPTCHA_HEADED === '1' ? false : true;
    const browser = await Camoufox({ headless } as any);
    const context = await browser.newContext();
    const page = await context.newPage();
    const start = Date.now();
    const finalScreenshot = path.resolve(
      __dirname, '..', `hcaptcha_5x_attempt_${i}.png`
    );
    let solved = false;
    let token = '';
    let err: string | undefined;

    try {
      const solver = new CaptchaKrakenSolver({
        repoPath: REPO_PATH,
        pythonCommand: PYTHON_COMMAND,
        model: MODEL,
        apiKey: API_KEY,
        maxSolveLoops: 25,
        postSolveDelayMs: 800,
        overallSolveTimeoutMs: 60_000,
      });

      await page.goto(HCAPTCHA_URL, { waitUntil: 'networkidle' });
      // Give the hCaptcha checkbox widget time to render before solving —
      // an early screenshot catches a blank/unpainted iframe.
      await page.waitForTimeout(2500);
      await solver.solve(page as any);

      token = await page.$eval(
        '[name="h-captcha-response"]',
        el => (el as HTMLTextAreaElement).value,
      ).catch(() => '');
      solved = token.length > 20;
    } catch (e: any) {
      err = e?.message ?? String(e);
    } finally {
      try {
        await page.screenshot({ path: finalScreenshot, fullPage: false });
      } catch {}
      await context.close();
      await browser.close();
    }

    results.push({
      i,
      solved,
      token: token.slice(0, 40),
      tokenLen: token.length,
      elapsedMs: Date.now() - start,
      finalScreenshot,
      error: err,
    });
  });
}
