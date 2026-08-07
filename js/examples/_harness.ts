/**
 * Shared runner for the CaptchaKraken TypeScript demos.
 *
 * Launches a real stealth browser (camoufox, using the binary from your fork —
 * JWriter20/camoufox releases; see README.md), navigates to a captcha demo
 * site, runs the full solver end-to-end, and prints a compact report: token
 * generation speed, total solve time, and whether the solve succeeded (with a
 * best-effort reason when it didn't).
 */
import { Camoufox } from 'camoufox-js';
import { CaptchaKrakenSolver } from '../src/index';
import type { SolveResult } from '../src/types';

export interface DemoSpec {
  name: string;
  url: string;
  vendor: 'recaptcha' | 'hcaptcha';
}

/**
 * Let any demo point at an arbitrary page:
 *
 *   npx tsx examples/demoHcaptcha.ts                      # built-in demo page
 *   npx tsx examples/demoHcaptcha.ts https://your.site/   # anything else
 *   npx tsx examples/demoHcaptcha.ts https://your.site/ --vendor recaptcha
 *
 * The vendor stays a default rather than being inferred from the URL: it only
 * selects the wording of a failure explanation, and the solver detects the
 * actual widget itself. Guessing from a hostname would be wrong exactly on the
 * pages worth demoing — your own site, embedding someone else's captcha.
 * Mirrors `spec_from_argv` in the Python harness.
 */
export function specFromArgv(base: DemoSpec, argv: string[] = process.argv.slice(2)): DemoSpec {
  const spec = { ...base };
  const rest: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--vendor' && argv[i + 1]) {
      spec.vendor = argv[++i] as DemoSpec['vendor'];
    } else if (argv[i] === '--name' && argv[i + 1]) {
      spec.name = argv[++i];
    } else if (argv[i] === '--headed') {
      process.env.HEADLESS = '0';
    } else if (!argv[i].startsWith('-')) {
      rest.push(argv[i]);
    }
  }
  if (rest.length) {
    spec.url = rest[0];
    if (!spec.name || spec.name === base.name) spec.name = rest[0];
  }
  return spec;
}

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

/** Best-effort human explanation when a solve doesn't succeed. */
function explain(vendor: string, result: SolveResult | void, err: unknown): string {
  const msg = err instanceof Error ? err.message : err ? String(err) : '';
  const low = msg.toLowerCase();

  if (low.includes('econnrefused') || low.includes('base_url') || low.includes('vllm') || low.includes('fetch failed')) {
    return 'Could not reach the vLLM server — is it running and is VLLM_BASE_URL correct? (It auto-starts locally only if captchakraken[serve] is installed.)';
  }
  if (low.includes('unsupported') || low.includes('cannot solve')) {
    return vendor === 'hcaptcha'
      ? 'The model could not produce a usable action for this challenge — most likely a video challenge (not supported) or a frame that had not finished rendering. Reload to try again.'
      : 'The model could not produce a usable action for this captcha — reload to try again.';
  }
  if (low.includes('timeout')) {
    return 'Timed out — the page or the challenge iframe never became interactable (slow network, or the widget was blocked).';
  }
  if (result && !result.isSolved) {
    return 'Tiles were selected but the provider did not accept the answer. Most often this is IP reputation / fingerprint flagging: providers reject even correct answers from a distrusted IP. Try a cleaner IP or a residential proxy.';
  }
  return msg || 'Unknown failure.';
}

function report(spec: DemoSpec, opts: {
  ok: boolean;
  totalMs: number;
  solveMs: number;
  result?: SolveResult | void;
  reason?: string;
}): void {
  const { ok, totalMs, solveMs, result, reason } = opts;
  const out = result ? result.tokenUsage.outputTokens : 0;
  const inp = result ? result.tokenUsage.inputTokens : 0;
  const tps = out > 0 && solveMs > 0 ? out / (solveMs / 1000) : 0;

  const line = '─'.repeat(52);
  console.log(`\n${line}`);
  console.log(`  CaptchaKraken demo — ${spec.name}`);
  console.log(`  ${spec.url}`);
  console.log(line);
  console.log(`  result        : ${ok ? '✓ SOLVED' : '✗ not solved'}`);
  console.log(`  total time    : ${fmtMs(totalMs)}   (solve: ${fmtMs(solveMs)})`);
  console.log(`  tokens        : ${inp} in / ${out} out`);
  console.log(`  gen speed     : ${tps > 0 ? `~${tps.toFixed(1)} tok/s (end-to-end approx)` : 'n/a'}`);
  if (!ok && reason) console.log(`  reason        : ${reason}`);
  console.log(`${line}\n`);
}

export async function runDemo(baseSpec: DemoSpec): Promise<void> {
  const spec = specFromArgv(baseSpec);
  const t0 = Date.now();
  const executablePath = process.env.CAMOUFOX_BINARY || process.env.CAMOUFOX_EXECUTABLE_PATH;
  const headless = process.env.HEADLESS !== '0';

  let browser: Awaited<ReturnType<typeof Camoufox>> | undefined;
  let context: any;
  try {
    browser = await Camoufox({
      headless,
      // HUMANIZE defaults OFF — see the Python harness's _launch_kwargs for the
      // measurement. The solver already walks its own 60-point trajectory;
      // camoufox's humanize juggler re-humanises each of those 60 micro-moves,
      // turning one straight line into 60 nested traversals. That is 25-52s per
      // click round instead of ~5s, which overruns the solve timeout and
      // reports a solvable captcha as unsolved. HUMANIZE=1 to opt back in.
      humanize: process.env.HUMANIZE === '1',
      geoip: false,
      // Point camoufox at YOUR fork's binary. If unset, camoufox-js uses its
      // default cached binary (`npx camoufox-js fetch`).
      ...(executablePath ? { executable_path: executablePath } : {}),
    });
    // camoufox sets its own viewport via the fingerprint, so DON'T let playwright
    // emulate one (viewport: null) — passing a viewport trips camoufox 152's
    // Juggler on the `isMobile` field.
    // Record the session when CAPTCHA_DEMO_VIDEO_DIR is set — same env contract
    // as the Python harness. Only a build whose screencast emits frames
    // produces anything; stock camoufox writes nothing or a blank file with no
    // error, so a caller that cares should check the size of what it gets.
    const videoDir = process.env.CAPTCHA_DEMO_VIDEO_DIR;
    const [vw, vh] = (process.env.CAPTCHA_DEMO_VIDEO_SIZE || '1280x800').split('x').map(Number);
    context = await (browser as any).newContext({
      viewport: null,
      ...(videoDir ? { recordVideo: { dir: videoDir, size: { width: vw, height: vh } } } : {}),
    });
    const page = await context.newPage();
    await page.goto(spec.url, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    // Let the captcha widget's iframe inject + render before we look for it
    // (reCAPTCHA/hCaptcha load async, after DOMContentLoaded).
    await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {});
    await page.waitForTimeout(3000);

    const s0 = Date.now();
    let result: SolveResult | void;
    let reason: string | undefined;
    try {
      result = await new CaptchaKrakenSolver().solve(page);
    } catch (err) {
      const solveMs = Date.now() - s0;
      report(spec, { ok: false, totalMs: Date.now() - t0, solveMs, reason: explain(spec.vendor, undefined, err) });
      process.exitCode = 1;
      return;
    }
    const solveMs = Date.now() - s0;

    const ok = !!result && result.isSolved;
    if (!ok) reason = explain(spec.vendor, result, undefined);
    report(spec, { ok, totalMs: Date.now() - t0, solveMs, result, reason });
    process.exitCode = ok ? 0 : 1;
  } catch (err) {
    report(spec, { ok: false, totalMs: Date.now() - t0, solveMs: 0, reason: explain(spec.vendor, undefined, err) });
    process.exitCode = 1;
  } finally {
    // Close the CONTEXT before the browser: playwright finalises the video on
    // context close, so skipping it leaves a file that never appears.
    try { await context?.close(); } catch { /* ignore */ }
    try { await (browser as any)?.close(); } catch { /* ignore */ }
  }
}
