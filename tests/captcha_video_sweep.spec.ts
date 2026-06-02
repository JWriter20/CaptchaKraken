import { test } from '@playwright/test';
import { CaptchaKrakenSolver } from '../src/solver.js';
import { execFile } from 'child_process';
import { promisify } from 'util';
import * as path from 'path';
import * as dotenv from 'dotenv';
import * as fs from 'fs';
import { Camoufox } from '@jobharvest/camoufox-js';

const execFileAsync = promisify(execFile);

dotenv.config();
dotenv.config({ path: path.resolve(__dirname, '..', '..', '.env') });

const REPO_PATH = process.env.CAPTCHA_KRAKEN_REPO_PATH
  ?? path.resolve(__dirname, '..', 'CaptchaKraken-cli');
const PYTHON_COMMAND = process.env.PYTHON_COMMAND
  ?? path.join(REPO_PATH, '.venv', 'bin', 'python');
const MODEL = process.env.CAPTCHA_LORA_NAME || 'captcha';
const API_KEY = process.env.CAPTCHA_KRAKEN_API_KEY || process.env.VLLM_API_KEY;

const HCAPTCHA_URL = process.env.HCAPTCHA_DEMO_URL ?? 'https://accounts.hcaptcha.com/demo';
const RECAPTCHA_URL = process.env.RECAPTCHA_DEMO_URL ?? 'https://www.google.com/recaptcha/api2/demo';
const N = parseInt(process.env.SAMPLE_N || '5', 10);

const VIDEOS_DIR = path.resolve(__dirname, '..', 'captcha_videos');
const SUMMARY_PATH = path.resolve(__dirname, '..', 'captcha_video_summary.json');

fs.mkdirSync(VIDEOS_DIR, { recursive: true });

test.describe.configure({ mode: 'serial' });

interface Outcome {
  vendor: 'hcaptcha' | 'recaptcha';
  i: number;
  solved: boolean;
  tokenLen: number;
  elapsedMs: number;
  finalScreenshot: string;
  video: string | null;
  error?: string;
}
const results: Outcome[] = [];

test.afterAll(() => {
  fs.writeFileSync(SUMMARY_PATH, JSON.stringify(results, null, 2));
  console.log('\n=== Captcha video sweep summary ===');
  for (const r of results) {
    console.log(
      `  ${r.vendor} #${r.i}: ${r.solved ? 'PASS' : 'FAIL'} ` +
      `(token len ${r.tokenLen}, ${(r.elapsedMs / 1000).toFixed(1)}s) ` +
      `screenshot=${r.finalScreenshot} video=${r.video ?? '(none)'}` +
      (r.error ? `   error: ${r.error}` : '')
    );
  }
  const byVendor = (v: string) => results.filter(r => r.vendor === v);
  const h = byVendor('hcaptcha');
  const re = byVendor('recaptcha');
  console.log(`hCaptcha pass rate: ${h.filter(r => r.solved).length}/${h.length}`);
  console.log(`reCAPTCHA pass rate: ${re.filter(r => r.solved).length}/${re.length}`);
  console.log(`Summary written to ${SUMMARY_PATH}`);
  console.log(`Videos in ${VIDEOS_DIR}`);
});

/**
 * Roll our own video by polling page.screenshot() at ~5 fps into a frames
 * directory, then encoding to MP4 with OpenCV (already in the CLI venv).
 * Camoufox's built-in recordVideo writes blank placeholders, so we can't
 * use it.
 */
class FrameRecorder {
  private framesDir: string;
  private frameIdx = 0;
  private timer: NodeJS.Timeout | null = null;
  private inflight = false;
  constructor(private page: any, dir: string, private fps = 5) {
    this.framesDir = dir;
    fs.mkdirSync(dir, { recursive: true });
  }
  start() {
    const intervalMs = Math.round(1000 / this.fps);
    this.timer = setInterval(async () => {
      if (this.inflight) return; // never queue up
      this.inflight = true;
      try {
        const fp = path.join(this.framesDir,
          String(this.frameIdx).padStart(5, '0') + '.png');
        await this.page.screenshot({ path: fp, fullPage: false });
        this.frameIdx++;
      } catch {
        // page may be closing
      } finally {
        this.inflight = false;
      }
    }, intervalMs);
  }
  async stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    // Give any in-flight screenshot up to 2s to finish.
    const deadline = Date.now() + 2000;
    while (this.inflight && Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 50));
    }
  }
  async encode(outPath: string): Promise<boolean> {
    if (this.frameIdx === 0) return false;
    // Use the CLI's venv python (already has cv2 installed).
    const script = `
import cv2, glob, sys, os
frames = sorted(glob.glob(os.path.join(${JSON.stringify(this.framesDir)}, "*.png")))
if not frames: sys.exit(1)
first = cv2.imread(frames[0])
h, w = first.shape[:2]
fourcc = cv2.VideoWriter_fourcc(*"mp4v")
out = cv2.VideoWriter(${JSON.stringify(outPath)}, fourcc, ${this.fps}, (w, h))
for fp in frames:
    img = cv2.imread(fp)
    if img is None: continue
    if img.shape[:2] != (h, w):
        img = cv2.resize(img, (w, h))
    out.write(img)
out.release()
print(f"encoded {len(frames)} frames -> {${JSON.stringify(outPath)}}")
`;
    const scriptPath = path.join(this.framesDir, 'encode.py');
    fs.writeFileSync(scriptPath, script);
    try {
      await execFileAsync(PYTHON_COMMAND, [scriptPath], { timeout: 60_000 });
      // Clean up the frames after successful encode.
      fs.rmSync(this.framesDir, { recursive: true, force: true });
      return true;
    } catch (e) {
      console.warn(`Video encode failed: ${e}`);
      return false;
    }
  }
}

async function runOnce(
  vendor: 'hcaptcha' | 'recaptcha',
  i: number,
): Promise<Outcome> {
  const headless: boolean | 'virtual' =
    process.platform === 'linux' ? 'virtual' :
    process.env.CAPTCHA_HEADED === '1' ? false : true;
  const browser = await Camoufox({ headless } as any);
  const context = await browser.newContext();
  const page = await context.newPage();
  const start = Date.now();

  const finalScreenshot = path.resolve(
    __dirname, '..', `${vendor}_video_attempt_${i}.png`,
  );
  const videoPath = path.join(VIDEOS_DIR, `${vendor}_attempt_${i}.mp4`);
  const framesDir = path.join(VIDEOS_DIR, `${vendor}_${i}_frames`);
  const recorder = new FrameRecorder(page, framesDir, 5);

  let solved = false;
  let tokenLen = 0;
  let err: string | undefined;

  try {
    const url = vendor === 'hcaptcha' ? HCAPTCHA_URL : RECAPTCHA_URL;
    await page.goto(url);
    // Start recording AFTER navigation so the video starts on the demo page.
    recorder.start();

    const solver = new CaptchaKrakenSolver({
      repoPath: REPO_PATH,
      pythonCommand: PYTHON_COMMAND,
      model: MODEL,
      apiKey: API_KEY,
      maxSolveLoops: 25,
      postSolveDelayMs: 800,
      overallSolveTimeoutMs: 120_000,
    });
    await solver.solve(page as any);

    const selector = vendor === 'hcaptcha'
      ? '[name="h-captcha-response"]'
      : 'textarea#g-recaptcha-response, textarea[name="g-recaptcha-response"]';
    const token = await page.$eval(
      selector,
      el => (el as HTMLTextAreaElement).value,
    ).catch(() => '');
    tokenLen = token.length;
    solved = token.length > 20;
  } catch (e: any) {
    err = e?.message ?? String(e);
  } finally {
    await recorder.stop();
    try {
      await page.screenshot({ path: finalScreenshot, fullPage: false });
    } catch {}
    await context.close();
    await browser.close();
  }

  const ok = await recorder.encode(videoPath);

  return {
    vendor,
    i,
    solved,
    tokenLen,
    elapsedMs: Date.now() - start,
    finalScreenshot,
    video: ok ? videoPath : null,
    error: err,
  };
}

for (let i = 1; i <= N; i++) {
  test(`hCaptcha attempt ${i}`, async () => {
    test.slow();
    results.push(await runOnce('hcaptcha', i));
  });
}

for (let i = 1; i <= N; i++) {
  test(`reCAPTCHA attempt ${i}`, async () => {
    test.slow();
    results.push(await runOnce('recaptcha', i));
  });
}
