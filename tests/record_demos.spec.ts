/**
 * record_demos.spec.ts — produce the README's success-rate numbers AND the
 * demo videos in one pass, driving the REAL browser against the live vLLM
 * server.
 *
 * For each target type we attempt up to MAX_ATTEMPTS real solves and keep
 * recording until we reach SAMPLE_N *eligible* attempts:
 *
 *   - recaptcha_3x3 / recaptcha_4x4 : reCAPTCHA's demo serves whichever grid it
 *     wants, so we run the reCAPTCHA flow and tag each completed attempt by the
 *     grid size the solver actually established (3 → 3x3, 4 → 4x4). An attempt
 *     counts toward whichever bucket it landed in.
 *   - hcaptcha : hCaptcha's demo serves many non-grid puzzles (drag, path, …)
 *     that are OUT OF SCOPE. If an attempt never reaches a grid we DISCARD it
 *     (it doesn't count toward SAMPLE_N) and immediately retry, up to the
 *     attempt budget. Only grid-bearing attempts are scored.
 *
 * Videos are kept only for SOLVED attempts (that's what the README embeds);
 * unsolved frames are discarded to keep the repo light. A per-type summary with
 * solve rates is written to record_demos_summary.json.
 *
 * Run (needs the local vLLM server up — see install.sh):
 *   VLLM_BASE_URL=http://localhost:8000/v1 \
 *   CAPTCHA_LORA_NAME=captcha-grid \
 *   VLLM_API_KEY=... \
 *   SAMPLE_N=25 \
 *   npx playwright test tests/record_demos.spec.ts
 */
import { test } from '@playwright/test';
import { CaptchaKrakenSolver } from '../src/solver.js';
import { execFile } from 'child_process';
import { promisify } from 'util';
import * as path from 'path';
import * as dotenv from 'dotenv';
import * as fs from 'fs';
import { Camoufox } from '@jobharvest/camoufox-js';
import type { SolveStepEvent } from '../src/types.js';

const execFileAsync = promisify(execFile);

dotenv.config();
dotenv.config({ path: path.resolve(__dirname, '..', '..', '.env') });

const REPO_PATH = process.env.CAPTCHA_KRAKEN_REPO_PATH
  ?? path.resolve(__dirname, '..', 'CaptchaKraken-cli');
const PYTHON_COMMAND = process.env.PYTHON_COMMAND
  ?? path.join(REPO_PATH, '.venv', 'bin', 'python');
const MODEL = process.env.CAPTCHA_LORA_NAME || 'captcha-grid';
const API_KEY = process.env.CAPTCHA_KRAKEN_API_KEY || process.env.VLLM_API_KEY;

const HCAPTCHA_URL = process.env.HCAPTCHA_DEMO_URL ?? 'https://accounts.hcaptcha.com/demo';
const RECAPTCHA_URL = process.env.RECAPTCHA_DEMO_URL ?? 'https://www.google.com/recaptcha/api2/demo';

/** Eligible (in-scope, grid-bearing) attempts to collect per family. */
const SAMPLE_N = parseInt(process.env.SAMPLE_N || '25', 10);
/** Hard ceiling on real attempts per family so a bad streak can't run forever. */
const MAX_ATTEMPTS = parseInt(process.env.MAX_ATTEMPTS || String(SAMPLE_N * 3), 10);

const VIDEOS_DIR = path.resolve(__dirname, '..', 'captcha_videos');
const SUMMARY_PATH = path.resolve(__dirname, '..', 'record_demos_summary.json');

fs.mkdirSync(VIDEOS_DIR, { recursive: true });
// NOT serial: a crash collecting one family must not skip the other. Each test
// disables its own timeout inside the body (top-level setTimeout(0) is unreliable).

type Bucket = 'recaptcha_3x3' | 'recaptcha_4x4' | 'hcaptcha' | 'discarded';

interface Outcome {
  family: 'recaptcha' | 'hcaptcha';
  bucket: Bucket;
  attempt: number;
  solved: boolean;
  tokenLen: number;
  elapsedMs: number;
  video: string | null;
  error?: string;
}
const results: Outcome[] = [];

function rate(bucket: Bucket) {
  const r = results.filter(x => x.bucket === bucket);
  const ok = r.filter(x => x.solved).length;
  return { ok, n: r.length };
}

test.afterAll(() => {
  const buckets: Bucket[] = ['recaptcha_3x3', 'recaptcha_4x4', 'hcaptcha'];
  const report = {
    sampleTarget: SAMPLE_N,
    perType: Object.fromEntries(buckets.map(b => {
      const { ok, n } = rate(b);
      return [b, { solved: ok, attempts: n, rate: n ? +(ok / n).toFixed(3) : null }];
    })),
    discarded: results.filter(x => x.bucket === 'discarded').length,
    raw: results,
  };
  fs.writeFileSync(SUMMARY_PATH, JSON.stringify(report, null, 2));
  console.log('\n=== record_demos summary ===');
  for (const b of buckets) {
    const { ok, n } = rate(b);
    console.log(`  ${b}: ${ok}/${n} solved` + (n ? ` (${((ok / n) * 100).toFixed(1)}%)` : ''));
  }
  console.log(`  hcaptcha non-grid attempts discarded: ${report.discarded}`);
  console.log(`Summary → ${SUMMARY_PATH}`);
  console.log(`Solved videos → ${VIDEOS_DIR}`);
});

/** Poll page.screenshot() at ~5fps into frames, then encode to MP4 with cv2. */
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
      if (this.inflight) return;
      this.inflight = true;
      try {
        const fp = path.join(this.framesDir, String(this.frameIdx).padStart(5, '0') + '.png');
        await this.page.screenshot({ path: fp, fullPage: false });
        this.frameIdx++;
      } catch { /* page closing */ } finally { this.inflight = false; }
    }, intervalMs);
  }
  async stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    const deadline = Date.now() + 2000;
    while (this.inflight && Date.now() < deadline) await new Promise(r => setTimeout(r, 50));
  }
  discard() { fs.rmSync(this.framesDir, { recursive: true, force: true }); }
  async encode(outPath: string): Promise<boolean> {
    if (this.frameIdx === 0) { this.discard(); return false; }
    // Encode to H.264 + yuv420p + faststart so the clip plays in browsers and
    // embeds inline on GitHub (drag the .mp4 into an issue/PR → user-attachments
    // URL → README <video>). OpenCV's mp4v/FMP4 writer produces files browsers
    // REFUSE to play ("No video with supported format"), so we feed the PNG
    // frames to ffmpeg (the static binary bundled with imageio-ffmpeg) instead,
    // and fall back to the mp4v writer only if ffmpeg isn't importable.
    const script = `
import cv2, glob, sys, os, subprocess
frames = sorted(glob.glob(os.path.join(${JSON.stringify(this.framesDir)}, "*.png")))
if not frames: sys.exit(1)
h, w = cv2.imread(frames[0]).shape[:2]
out_path = ${JSON.stringify(outPath)}
fps = ${this.fps}
try:
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    # h//2*2, w//2*2: yuv420p needs even dimensions.
    H, W = h - h % 2, w - w % 2
    p = subprocess.Popen([ff, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps),
        "-i", "-", "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-crf", "23", "-movflags", "+faststart", out_path],
        stdin=subprocess.PIPE)
    for fp in frames:
        img = cv2.imread(fp)
        if img is None: continue
        if img.shape[:2] != (h, w): img = cv2.resize(img, (w, h))
        p.stdin.write(img[:H, :W].tobytes())
    p.stdin.close(); p.wait()
    if p.returncode != 0: raise RuntimeError("ffmpeg failed")
    print(f"encoded {len(frames)} frames (h264) -> {out_path}")
except Exception as e:
    # Fallback: OpenCV mp4v (NOT browser-playable, but better than nothing).
    sys.stderr.write(f"h264 encode failed ({e}); falling back to mp4v\\n")
    out = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for fp in frames:
        img = cv2.imread(fp)
        if img is None: continue
        if img.shape[:2] != (h, w): img = cv2.resize(img, (w, h))
        out.write(img)
    out.release()
    print(f"encoded {len(frames)} frames (mp4v fallback) -> {out_path}")
`;
    const scriptPath = path.join(this.framesDir, 'encode.py');
    fs.writeFileSync(scriptPath, script);
    try {
      await execFileAsync(PYTHON_COMMAND, [scriptPath], { timeout: 60_000 });
      this.discard();
      return true;
    } catch (e) {
      console.warn(`Video encode failed: ${e}`);
      this.discard();
      return false;
    }
  }
}

/** Which grid we're hunting for: 'any', '3x3', or '4x4'. */
const WANT_GRID = (process.env.WANT_GRID || 'any').toLowerCase();
/** Max times to click the reCAPTCHA reload button to cycle to the wanted grid. */
const MAX_SKIPS = parseInt(process.env.MAX_SKIPS || '12', 10);

/**
 * Open the reCAPTCHA challenge and, if WANT_GRID is set, click the reload/skip
 * button (`#recaptcha-reload-button`) to cycle challenges in the SAME session
 * until the wanted grid size appears — instead of tearing the browser down and
 * re-rolling, which doesn't change reCAPTCHA's session-driven 3x3-vs-4x4 choice.
 *
 * Returns the grid size found (3 | 4 | null). Best-effort: a missing frame /
 * button just falls through and lets the solver run on whatever's shown.
 */
async function cycleRecaptchaToGrid(page: any, want: string): Promise<3 | 4 | null> {
  // Click the anchor checkbox to open the challenge.
  const anchor = await page.$('iframe[src*="recaptcha/api2/anchor"]');
  if (anchor) {
    const f = await anchor.contentFrame();
    const box = await f?.$('.recaptcha-checkbox');
    if (box) { await box.click().catch(() => {}); }
  }

  const wantSize = want === '4x4' ? 4 : want === '3x3' ? 3 : null;

  // Read the challenge grid size from the bframe table classes (rc-imageselect-
  // table-33 = 3x3, -44 = 4x4); fall back to counting tiles.
  const readSize = async (): Promise<3 | 4 | null> => {
    const bframe = await page.$('iframe[src*="recaptcha/api2/bframe"]');
    if (!bframe) return null;
    const f = await bframe.contentFrame();
    if (!f) return null;
    try {
      if (await f.$('.rc-imageselect-table-44')) return 4;
      if (await f.$('.rc-imageselect-table-33')) return 3;
      const n = await f.$$eval('.rc-imageselect-tile', els => els.length).catch(() => 0);
      if (n >= 16) return 4;
      if (n >= 9) return 3;
    } catch { /* frame detached mid-read */ }
    return null;
  };

  let size = await waitFor(readSize, 8000);
  if (!wantSize) return size; // 'any' — take whatever opened.

  for (let i = 0; i < MAX_SKIPS && size !== wantSize; i++) {
    const bframe = await page.$('iframe[src*="recaptcha/api2/bframe"]');
    const f = bframe ? await bframe.contentFrame() : null;
    const reload = f ? await f.$('#recaptcha-reload-button') : null;
    if (!reload) break;
    await reload.click().catch(() => {});
    await page.waitForTimeout(900); // let the new challenge paint
    size = await waitFor(readSize, 6000);
  }
  return size;
}

/**
 * Open the hCaptcha challenge and click its refresh button (`.refresh.button`)
 * to cycle PAST out-of-scope puzzles (drag / path / "choose the card…") until a
 * 3x3 property grid ("click each image containing/with a …") appears — same
 * same-session skipping idea as reCAPTCHA. Returns true if a grid was reached.
 */
async function cycleHcaptchaToGrid(page: any): Promise<boolean> {
  const cb = await page.$('iframe[src*="hcaptcha"][src*="frame=checkbox"]');
  if (cb) {
    const f = await cb.contentFrame();
    await f?.click('#checkbox').catch(() => {});
  }

  // A property grid: prompt is "select/click each image …" AND a 3x3 task grid
  // of image cells is present. Non-grid puzzles ("choose the card…", drag, path)
  // fail this and get refreshed away.
  const isGrid = async (): Promise<boolean> => {
    const ch = await page.$('iframe[src*="hcaptcha"][src*="frame=challenge"]');
    if (!ch) return false;
    const f = await ch.contentFrame();
    if (!f) return false;
    return f.evaluate(() => {
      const p = (document.querySelector('.prompt-text, h2') as HTMLElement)?.innerText?.toLowerCase() || '';
      const gridish = /click each|select each|each image (containing|with)/.test(p);
      const cells = document.querySelectorAll('.task-image, .image-wrapper .image, .task').length;
      return gridish && cells >= 9;
    }).catch(() => false);
  };
  const refresh = async (): Promise<boolean> => {
    const ch = await page.$('iframe[src*="hcaptcha"][src*="frame=challenge"]');
    const f = ch ? await ch.contentFrame() : null;
    const btn = f ? await f.$('.refresh.button, .refresh') : null;
    if (!btn) return false;
    await btn.click().catch(() => {});
    await page.waitForTimeout(900);
    return true;
  };

  if (await waitFor(isGrid, 8000)) return true;
  for (let i = 0; i < MAX_SKIPS; i++) {
    if (!(await refresh())) break;
    if (await waitFor(isGrid, 5000)) return true;
  }
  return false;
}

/** Poll fn() until it returns truthy or the timeout elapses. */
async function waitFor<T>(fn: () => Promise<T>, ms: number): Promise<T | null> {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    const v = await fn().catch(() => null);
    if (v) return v;
    await new Promise(r => setTimeout(r, 300));
  }
  return (await fn().catch(() => null)) ?? null;
}

/**
 * One real attempt. Returns the outcome with the bucket the solver actually
 * landed in. `sawGrid` reflects whether any grid round was observed (used to
 * discard out-of-scope hCaptcha puzzles).
 */
async function runOnce(
  family: 'recaptcha' | 'hcaptcha',
  attempt: number,
): Promise<{ outcome: Omit<Outcome, 'video'>; recorder: FrameRecorder; sawGrid: boolean }> {
  const headless: boolean | 'virtual' =
    process.platform === 'linux' ? 'virtual'
      : process.env.CAPTCHA_HEADED === '1' ? false : true;
  const browser = await Camoufox({ headless } as any);
  const context = await browser.newContext();
  const page = await context.newPage();
  const start = Date.now();

  const framesDir = path.join(VIDEOS_DIR, `${family}_${attempt}_frames`);
  const recorder = new FrameRecorder(page, framesDir, 5);

  // Observe grid presence + size via onStep. reCAPTCHA emits `round` steps for
  // the 3x3 dynamic driver and a one-shot for 4x4; hCaptcha grids surface as a
  // recaptcha-style grid round too. We read the grid size out of step.meta when
  // present and fall back to the puzzleSource for the family tag.
  let sawGrid = false;
  let gridSize: 3 | 4 | null = null;
  const onStep = (e: SolveStepEvent) => {
    const sz = e.meta?.gridSize ?? e.meta?.size ?? e.meta?.rows;
    if (typeof sz === 'number' && (sz === 3 || sz === 4)) { sawGrid = true; gridSize = sz; }
    if (e.stage === 'round') sawGrid = true;
    if (typeof e.label === 'string' && /\bgrid\b|\btile/i.test(e.label)) sawGrid = true;
  };

  let solved = false, tokenLen = 0;
  let err: string | undefined;

  try {
    await page.goto(family === 'hcaptcha' ? HCAPTCHA_URL : RECAPTCHA_URL);
    recorder.start();

    // Cycle hCaptcha PAST non-grid puzzles to a property grid (same-session
    // refresh) instead of discarding+re-rolling. Disable with HCAPTCHA_CYCLE=0.
    if (family === 'hcaptcha' && process.env.HCAPTCHA_CYCLE !== '0') {
      const reached = await cycleHcaptchaToGrid(page);
      if (reached) sawGrid = true;
      if (!reached) {
        await recorder.stop(); recorder.discard();
        await context.close(); await browser.close();
        return {
          outcome: { family, bucket: 'discarded', attempt, solved: false, tokenLen: 0,
            elapsedMs: Date.now() - start, error: 'no hCaptcha grid after skips' },
          recorder, sawGrid: false,
        };
      }
    }

    // Cycle reCAPTCHA challenges (via the reload button) to the wanted grid type
    // before solving — keeps us in one session instead of re-rolling the browser.
    if (family === 'recaptcha' && WANT_GRID !== 'any') {
      const cycled = await cycleRecaptchaToGrid(page, WANT_GRID);
      if (cycled) gridSize = cycled;
      const target = WANT_GRID === '4x4' ? 4 : 3;
      if (cycled !== target) {
        // Couldn't reach the wanted grid within MAX_SKIPS — bail this attempt so
        // it doesn't count against the wanted bucket.
        await recorder.stop(); recorder.discard();
        await context.close(); await browser.close();
        return {
          outcome: { family, bucket: 'discarded', attempt, solved: false, tokenLen: 0,
            elapsedMs: Date.now() - start, error: `wanted ${WANT_GRID}, got ${cycled ?? 'none'} after skips` },
          recorder, sawGrid: false,
        };
      }
    }

    const solver = new CaptchaKrakenSolver({
      repoPath: REPO_PATH,
      pythonCommand: PYTHON_COMMAND,
      model: MODEL,
      apiKey: API_KEY,
      maxSolveLoops: 25,
      postSolveDelayMs: 800,
      overallSolveTimeoutMs: 120_000,
      onStep,
    });
    await solver.solve(page as any);

    const selector = family === 'hcaptcha'
      ? '[name="h-captcha-response"]'
      : 'textarea#g-recaptcha-response, textarea[name="g-recaptcha-response"]';
    const token = await page.$eval(selector, el => (el as HTMLTextAreaElement).value).catch(() => '');
    tokenLen = token.length;
    solved = token.length > 20;
  } catch (e: any) {
    err = e?.message ?? String(e);
    // "no supported grid or checkbox" / "performed no interactions" ⇒ the demo
    // served an out-of-scope puzzle; leave sawGrid false so the caller discards.
  } finally {
    await recorder.stop();
    await context.close();
    await browser.close();
  }

  // Decide the bucket.
  let bucket: Bucket;
  if (family === 'recaptcha') {
    bucket = gridSize === 4 ? 'recaptcha_4x4' : 'recaptcha_3x3';
  } else {
    bucket = sawGrid ? 'hcaptcha' : 'discarded';
  }

  return {
    outcome: { family, bucket, attempt, solved, tokenLen, elapsedMs: Date.now() - start, error: err },
    recorder,
    sawGrid: family === 'hcaptcha' ? sawGrid : true,
  };
}

async function collect(family: 'recaptcha' | 'hcaptcha') {
  let eligible = 0;
  let attempt = 0;
  // Wall-clock budget so a long streak of non-grid hCaptcha puzzles (or slow
  // timeouts) can't run forever. Override with FAMILY_BUDGET_MIN.
  const budgetMs = parseInt(process.env.FAMILY_BUDGET_MIN || '45', 10) * 60_000;
  const familyStart = Date.now();
  while (eligible < SAMPLE_N && attempt < MAX_ATTEMPTS && (Date.now() - familyStart) < budgetMs) {
    attempt++;
    let run;
    try {
      run = await runOnce(family, attempt);
    } catch (e: any) {
      // A browser/page crash must not abort the whole sweep — count it as a
      // failed eligible attempt and keep going.
      console.log(`  [${family} #${attempt}] crashed: ${(e?.message ?? e).toString().slice(0, 80)}`);
      eligible++;
      results.push({ family, bucket: family === 'recaptcha' ? 'recaptcha_3x3' : 'hcaptcha',
        attempt, solved: false, tokenLen: 0, elapsedMs: 0, video: null, error: String(e?.message ?? e) });
      continue;
    }
    const { outcome, recorder, sawGrid } = run;

    // Discard (don't count) when the demo served the wrong thing: a non-grid
    // hCaptcha puzzle, or a reCAPTCHA we couldn't cycle to the wanted grid type.
    if ((family === 'hcaptcha' && !sawGrid) || outcome.bucket === 'discarded') {
      recorder.discard();
      results.push({ ...outcome, bucket: 'discarded', video: null });
      console.log(`  [${family} #${attempt}] ${outcome.error?.startsWith('wanted') ? outcome.error : 'non-grid puzzle'} → discarded, retrying`);
      continue;
    }

    eligible++;
    let video: string | null = null;
    if (outcome.solved) {
      const out = path.join(VIDEOS_DIR, `${outcome.bucket}_solved_${eligible}.mp4`);
      video = (await recorder.encode(out)) ? out : null;
    } else {
      recorder.discard();
    }
    results.push({ ...outcome, video });
    console.log(`  [${family} #${attempt}] ${outcome.bucket}: ${outcome.solved ? 'SOLVED' : 'failed'} ` +
      `(${(outcome.elapsedMs / 1000).toFixed(1)}s)` + (outcome.error ? ` — ${outcome.error.slice(0, 80)}` : ''));
  }
}

// Skip a family with SKIP_RECAPTCHA=1 / SKIP_HCAPTCHA=1 (e.g. when a demo isn't
// serving grids, to avoid burning the whole budget on discards).
test('record reCAPTCHA demos', async () => {
  test.setTimeout(0); // the family loop owns its own timing
  test.skip(process.env.SKIP_RECAPTCHA === '1', 'SKIP_RECAPTCHA=1');
  await collect('recaptcha');
});
test('record hCaptcha demos', async () => {
  test.setTimeout(0);
  test.skip(process.env.SKIP_HCAPTCHA === '1', 'SKIP_HCAPTCHA=1');
  await collect('hcaptcha');
});
