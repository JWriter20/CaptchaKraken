// Playwright API types only — and our OWN structural copies, not a browser
// package's. The solver never launches a browser; the caller hands us a `Page`
// from whichever Playwright-compatible launcher they chose (vanilla `playwright`,
// `patchright`, `camoufox-js`, …). Typing against any one of those would pull it
// into consumers' trees and break across version skew, so instead we duck-type
// the exact slice of the Playwright surface the solver uses. Every real
// Playwright `Page`/`Frame`/`ElementHandle` structurally satisfies these. See
// playwright-types.ts.
import {
  PlaywrightPage as Page,
  PlaywrightElementHandle as ElementHandle,
  PlaywrightFrame as Frame,
} from './playwright-types';
import { generate_trajectory } from './trajectory.js';
import { exec, execFile, spawn, ChildProcessWithoutNullStreams } from 'child_process';
import { promisify } from 'util';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { createHash, randomUUID } from 'crypto';
import { CaptchaKrakenConfig, SolverResult, ClickAction, DragAction, TypeAction, CaptchaAction, SolveResult, CliResponse, TokenUsage, Vector, SolveStepEvent } from './types';
import { aggregateTokenUsage } from './token-usage';
import { parseApiError } from './errors';
import { DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS } from './limits';
import { solveSlideGeometry } from './slide-geometry';

const execAsync = promisify(exec);
const execFileAsync = promisify(execFile);

/**
 * Centre of an [x1, y1, x2, y2] 0–1 box, as the (x, y) 0–1 point the animated
 * wait gate compares around. The solver builds these boxes as a small square
 * around the model's point, so the centre recovers that point exactly.
 */
function bboxCenter(bbox: [number, number, number, number]): [number, number] {
  return [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2];
}

function getBundledCliRoot(): string {
  // When installed from npm, this file is in `<pkgRoot>/dist` (compiled) or `<pkgRoot>/src` (dev).
  // Published packages bundle the python engine at `<pkgRoot>/python` (copied in
  // by scripts/copy-python.mjs at build time). In the source monorepo it instead
  // lives at the sibling `../python`, so fall back to that for local dev/tests.
  const bundled = path.resolve(__dirname, '..', 'python');
  if (fs.existsSync(bundled)) return bundled;
  return path.resolve(__dirname, '..', '..', 'python');
}

function getVenvPython(cliRoot: string): string | null {
  const venvDir = path.join(cliRoot, '.venv');
  const candidates = [
    path.join(venvDir, 'bin', 'python'),
    path.join(venvDir, 'bin', 'python3'),
    path.join(venvDir, 'Scripts', 'python.exe'),
    path.join(venvDir, 'Scripts', 'python'),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return null;
}

// Env for spawning the python CLI. Prepend the bundled `python/src` to
// PYTHONPATH so `python -m captchakraken.cli` imports even when the postinstall
// `pip install` was skipped or failed (best-effort bootstrap). `extra` carries
// per-invocation values (currently the solve session id) and is applied last so
// it wins over the inherited environment.
function cliEnv(cliRoot: string, extra?: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const srcDir = path.join(cliRoot, 'src');
  const existing = process.env.PYTHONPATH;
  return {
    ...process.env,
    PYTHONPATH: existing ? `${srcDir}${path.delimiter}${existing}` : srcDir,
    ...extra,
  };
}

// Simple Vector interface for internal use moved to types.ts

interface TimedVector {
  x: number;
  y: number;
  timestamp?: number;
}

/** Cached geometry for one reCAPTCHA 3x3 dynamic-puzzle session. */
interface GridSession {
  /** Grid cell boxes in SCREENSHOT pixel space, row-major, 0-indexed array. */
  gridBoxes: number[][];
  /** Playwright element box in PAGE css px (for mouse coords). */
  elementBox: { x: number; y: number; width: number; height: number };
  /** screenshot px -> page px. */
  scaleX: number;
  scaleY: number;
  /** Screenshot dimensions the gridBoxes were computed against. */
  screenshotW: number;
  screenshotH: number;
}

/** Per-cell grid state from grid-cell-states-fixed (1-indexed cell numbers). */
interface GridCellStates {
  empty: number[];
  changing: number[];
  loaded: number[];
  selected: number[];
}

const log = (message: string, ...args: any[]) => console.log(`[Solver] ${message}`, ...args);
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

/**
 * Lifecycle state of the challenge the solver is driving. Tracked so behaviours
 * can be gated on it — in particular so we never feed a mid-transition or
 * still-loading frame to the model.
 *
 *   Detecting → Loading → Ready → Solving → Acting → Submitting → Transitioning
 *   → (Loading → …) | Solved
 *
 * `Animated` used to be the terminal "it's a video, give up" exit. It no longer
 * is: a challenge that never settles gets RECORDED and solved from keyframes, so
 * the normal path absorbs it. The state now means only that the recording was
 * impossible, or that `videoSolveEnabled` is off.
 */
enum CaptchaState {
  Detecting = 'detecting',
  Loading = 'loading',
  Ready = 'ready',
  Solving = 'solving',
  Acting = 'acting',
  Submitting = 'submitting',
  Transitioning = 'transitioning',
  Solved = 'solved',
  Animated = 'animated',
}

/**
 * Vendors with no checkbox/challenge split — one container, one interactive
 * surface. Checked in detectCaptcha() after the five hard-coded reCAPTCHA /
 * hCaptcha / Turnstile checks above, so those keep first refusal. Selectors
 * lifted from src/captchaCollection/sources.py, which already drives these 8
 * vendors nightly in the collector. Mirror of PYTHON_VENDOR_WIDGET_LOCATORS in
 * page_solver.py — keep both in the same order with the same selectors.
 */
const VENDOR_WIDGET_LOCATORS: ReadonlyArray<{ puzzleSource: string; selectors: string[] }> = [
  { puzzleSource: 'geetest', selectors: ['.geetest_box', '.geetest_panel_box', '.geetest_popup_window', '.geetest_widget'] },
  { puzzleSource: 'tencent', selectors: ['iframe#tcaptcha_iframe_dy', 'iframe[id*="tcaptcha"]', 'iframe[src*="captcha.gtimg.com"]', 'iframe[src*="captcha.qq.com"]'] },
  { puzzleSource: 'yidun', selectors: ['.yidun_panel', '.yidun'] },
  { puzzleSource: 'yandex', selectors: ['.CheckboxCaptcha'] },
  { puzzleSource: 'lemin', selectors: ['#lemin-cropped-captcha', '.lemin-captcha-popup'] },
  { puzzleSource: 'prosopo', selectors: ['.prosopo-modalInner', '.procaptcha-checkbox'] },
  { puzzleSource: 'mtcaptcha', selectors: ['.mtcap'] },
  { puzzleSource: 'botdetect', selectors: ['.BDC_CaptchaDiv'] },
];

/**
 * Where the answer goes, when it is not a click. Mirrors TEXT_INPUT_SELECTORS /
 * SLIDER_HANDLE_SELECTORS / DRAGGABLE_PIECE_SELECTORS in page_solver.py — keep
 * both in the same order with the same selectors.
 *
 * Ordered VENDOR-FIRST, GENERIC-LAST, and the driver takes the first visible
 * match. That order is the design: a named vendor selector is unambiguous,
 * while the generic patterns are guesses that happen to be right most of the
 * time. Trying the guess first would, on a page hosting a captcha *and* a login
 * form, type the captcha's answer into the username box.
 *
 * The generic tail is not a nicety either — it is what actually fires on most
 * pages. Vendors rename these classes without notice, and our own Tier 3
 * fixtures render neither vendor's DOM.
 */
const TEXT_INPUT_SELECTORS: ReadonlyArray<string> = [
  // BotDetect — the input is application-defined, so match the id fragment its
  // own docs and samples use (the three the nightly collector already drives).
  'input[id*=captchaCode]',
  'input#captchaCode',
  'input[id*=validateCaptcha]',
  '.BDC_CaptchaDiv input[type=text]',
  // MTCaptcha
  'input.mtcap-inputtext',
  '.mtcap input[type=text]',
  // Yandex SmartCaptcha
  '.AdvancedCaptcha-Input input',
  'input.Textinput-Control',
  'input[name="rep"]',
  // Generic — an input the page itself labels as the captcha answer.
  'input[name*="captcha" i]',
  'input[id*="captcha" i]',
  'input[aria-label*="captcha" i]',
  'input[placeholder*="code" i]',
  'input[autocomplete="off"][type=text]',
  // Last resort: the only text box in the widget. Scoped to the challenge
  // frame/container by the caller, never to the whole page — see findControl.
  'input[type=text]',
  'input:not([type])',
  'input[type=tel]',
  'textarea',
];

/**
 * The handle you drag on a puzzle-piece slider. NOT the piece: on every one of
 * these vendors the piece is inert decoration that the handle carries, so a
 * drag starting on the piece moves nothing at all.
 */
const SLIDER_HANDLE_SELECTORS: ReadonlyArray<string> = [
  // GeeTest v3 / v4
  '.geetest_slider_button',
  '.geetest_btn',
  '.geetest_slider .geetest_arrow',
  // Tencent
  '#tcaptcha_drag_thumb',
  '.tc-slider-normal',
  '[id*=slideBlock]',
  // Yidun (NetEase)
  '.yidun_slider',
  '.yidun_jigsaw',
  // Lemin
  '.lemin-slider-handle',
  '#lemin-cropped-captcha .slider',
  // Generic — an ARIA slider, or a class that says handle/thumb/button on a
  // track. `[draggable=true]` is deliberately absent: it is the HTML5
  // drag-and-drop opt-in, which fires dragstart rather than pointermove, and no
  // slider captcha uses it.
  '[role="slider"]',
  '[aria-valuenow]',
  '[class*="slider"][class*="btn"]',
  '[class*="slider"][class*="button"]',
  '[class*="slide"][class*="handle"]',
  '[class*="drag"][class*="thumb"]',
];

/**
 * Fallback for the sliderless members of the family. Lemin's "cropped" puzzle
 * has no track at all — you drag the piece itself onto the gap — and the model
 * answers it with the same sourceless drag, because from the picture the two
 * are indistinguishable. Tried only after SLIDER_HANDLE_SELECTORS finds nothing.
 */
const DRAGGABLE_PIECE_SELECTORS: ReadonlyArray<string> = [
  '.lemin-cropped-puzzle-piece',
  '#lemin-cropped-captcha canvas + canvas',
  '[class*="puzzle"][class*="piece"]',
  '[class*="jigsaw"]',
];

/**
 * Puzzle-piece slider tuning. Mirrors the `slide_*` fields of
 * PageSolverConfig in page_solver.py.
 */
const SLIDE_PROBE_OFFSETS_PX = [24, 64];
const SLIDE_TOLERANCE_PX = 2;
const SLIDE_MAX_CORRECTIONS = 2;

export class CaptchaKrakenSolver {
  private config: CaptchaKrakenConfig;
  private lastMousePosition: Vector; // Start at safe position
  private imageCounter: number = 0; // Track images sent to CLI for debugging
  private sessionDebugDir: string | null = null;
  // onStep instrumentation: monotonic step index + solve-start wall clock.
  // Reset at the top of each solveImpl() so indices/elapsed are per-solve.
  private stepIndex: number = 0;
  private solveStartMs: number = 0;
  // Dedicated dump dir for the reCAPTCHA 3x3 dynamic driver — frames + a JSONL
  // state log so the click/fade/wait flow can be diagnosed offline. Always set
  // (independent of CAPTCHA_DEBUG) so we capture the hard-to-reproduce timing.
  private gridDebugDir: string | null = null;
  private gridDebugSeq: number = 0;
  // Persistent CV worker (`python -m captchakraken.cli serve`) — one long-lived process
  // that answers find-grid / grid-cell-states polls over stdin/stdout, so the
  // hot poll loops pay one ~0.4s interpreter+cv2 import ONCE instead of per poll.
  private cvWorker: ChildProcessWithoutNullStreams | null = null;
  private cvWorkerReady: Promise<boolean> | null = null;
  private cvWorkerSeq: number = 0;
  private cvWorkerPending: Map<number, { resolve: (v: any) => void; reject: (e: any) => void }> = new Map();
  private cvWorkerBuf: string = '';
  // Per-solve cache of model responses keyed by (screenshot content hash +
  // puzzle source + retry mode). If the page hasn't changed since we last asked
  // the model about it, re-querying is wasted work — reuse the prior answer.
  // Cleared at the top of each solve. See getSolution().
  private solutionCache: Map<string, CliResponse> = new Map();
  // Current challenge lifecycle state (see CaptchaState). Diagnostic + used to
  // gate behaviours; transitions are logged via gridDebug when CAPTCHA_DEBUG=1.
  private state: CaptchaState = CaptchaState.Detecting;
  // Content hash of the challenge frame at the moment we last clicked Submit.
  // On the next attempt we wait for the frame to CHANGE from this (the expected
  // post-submit transition) before treating it as a fresh puzzle — so the shift
  // itself is never screenshotted and mis-read as a blank/unsupported frame.
  // Cleared once consumed. See solveSingle().
  private lastSubmitFrameHash: string | null = null;
  // Groups every inference round of ONE captcha into a single attempt for the
  // hosted API. A dynamic reCAPTCHA 3x3 re-solves after each click round, so one
  // solve() can fire up to `recaptchaMaxDynamicRounds` model calls; sharing a
  // session id lets the gateway bill them as one capped attempt rather than N
  // independent ones. Null outside a solve; ignored entirely when self-hosting.
  private solveSessionId: string | null = null;

  constructor(config: CaptchaKrakenConfig = {}) {
    this.config = config;
    this.lastMousePosition = config.startingMousePosition ?? { x: 100, y: 100 };
  }

  async solve(page: Page): Promise<SolveResult | void> {
    this.solveSessionId = randomUUID();
    try {
      return await this.solveImpl(page);
    } finally {
      // Always shut the persistent CV worker down when a solve ends (success,
      // failure, or timeout) so we never leak a python process between solves.
      this.teardownCvWorker();
      this.cvWorkerReady = null;
      // Clear last: a stale id leaking into the NEXT solve would merge two
      // separate captchas into one billable attempt.
      this.solveSessionId = null;
    }
  }

  private async solveImpl(page: Page): Promise<SolveResult | void> {
    const maxSolveLoops = this.config.maxSolveLoops ?? 10;
    const postSolveDelayMs = this.config.postSolveDelayMs ?? 1200;
    const overallSolveTimeoutMs = this.config.overallSolveTimeoutMs ?? 120_000;

    const start = Date.now();
    let cumulativeTokenUsage: TokenUsage[] = [];
    this.imageCounter = 0;
    this.stepIndex = 0;
    this.solveStartMs = start;
    this.solutionCache.clear();
    this.lastSubmitFrameHash = null;
    this.setState(CaptchaState.Detecting);

    // Initialize session debug directory if debugging is enabled
    if (process.env.CAPTCHA_DEBUG === '1') {
      const cliRoot = this.config.repoPath ?? getBundledCliRoot();
      const debugRunsDir = path.join(cliRoot, '..', 'debug_runs');
      if (!fs.existsSync(debugRunsDir)) {
        fs.mkdirSync(debugRunsDir, { recursive: true });
      }
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, -5);
      this.sessionDebugDir = path.join(debugRunsDir, `solve_${timestamp}`);
      fs.mkdirSync(this.sessionDebugDir, { recursive: true });
      log(`Session debug directory: ${this.sessionDebugDir}`);
    }

    // Set to "missed-tiles" for the next iteration when we detect that the
    // captcha vendor rejected our submission with an under-selection error
    // (reCAPTCHA "Please select all matching images"). Used once, then
    // cleared. If the error appears again after the retry, we abort —
    // burning loops on a stuck model only delays the inevitable fail.
    let pendingRetryMode: string | null = null;
    let alreadyRetriedRecaptchaError = false;
    // A blank/transitioning frame that slips past the settle gate can make the
    // model return "unsupported". If we've already interacted (so we're mid
    // multi-round, not on a genuinely unsupported first frame), actively wait
    // for the challenge to settle and retry — up to this budget — before
    // declaring the whole puzzle unsupported. One retry isn't enough when the
    // next round loads slowly (that's why some solves failed round 2 and others
    // didn't — it was a race).
    let unsupportedRetries = 0;
    const maxUnsupportedRetries = this.config.maxUnsupportedReSolves ?? 3;
    // A stale/detached challenge handle: after a submit, hCaptcha swaps the
    // challenge iframe for the next round while we still hold the old handle, so
    // a screenshot on it hangs then fails "not visible". Not a real failure —
    // re-detect the fresh challenge and retry, up to this budget.
    let staleElementRetries = 0;
    const maxStaleElementRetries = this.config.maxStaleElementRetries ?? 3;
    // Track whether we've interacted with the captcha at least once. Before any
    // interaction, a null detectCaptcha() means "not rendered yet", not "solved".
    let hasInteracted = false;
    // Bounded wait for an in-DOM-but-still-rendering widget (Stage 1).
    let renderWaits = 0;
    const MAX_RENDER_WAITS = 6;

    for (let attempt = 1; attempt <= maxSolveLoops; attempt++) {
      if (Date.now() - start > overallSolveTimeoutMs) {
        throw new Error(`Captcha solve timed out after ${overallSolveTimeoutMs}ms (attempt ${attempt}/${maxSolveLoops}).`);
      }

      const captchaElement = await this.detectCaptcha(page);
      if (!captchaElement) {
        // Two-stage detection. detectCaptcha() returns null when there's no
        // VISIBLE, unsolved widget — but that splits into two cases:
        if (hasInteracted) {
          // We already clicked/solved something and now nothing actionable
          // remains → treat as solved.
          console.log('No supported captcha found (post-interaction); considering solved.');
          return {
            isSolved: true,
            finalMousePosition: this.lastMousePosition,
            tokenUsage: aggregateTokenUsage(cumulativeTokenUsage)
          };
        }

        // No interaction yet. Stage 1: is an interactive widget present in the
        // DOM but simply not finished rendering?
        if (await this.hasInteractiveWidgetInDom(page) && renderWaits < MAX_RENDER_WAITS) {
          renderWaits++;
          console.log(
            `Captcha widget present in DOM but not yet rendered; waiting `
            + `(${renderWaits}/${MAX_RENDER_WAITS}).`
          );
          await delay(800 + Math.random() * 300);
          continue;
        }

        // No interactive widget in the DOM (reCAPTCHA v3 / invisible, or an
        // hCaptcha that only triggers on user action), or it never rendered.
        // Fail fast rather than burning the whole loop budget.
        throw new Error(
          'No interactive captcha widget detected (likely reCAPTCHA v3 / '
          + 'invisible or a click-triggered challenge). Failing fast.'
        );
      }

      console.log(`\n--- Captcha Solve Loop ${attempt}/${maxSolveLoops} ---`);
      const retryModeThisLoop = pendingRetryMode;
      pendingRetryMode = null;

      let didInteract: boolean;
      let tokenUsage: TokenUsage[];
      try {
        ({ didInteract, tokenUsage } = await this.solveSingle(
          page, captchaElement, attempt, retryModeThisLoop,
        ));
      } catch (e: any) {
        // `.animated` no longer means "the challenge moves" — moving challenges are
        // recorded and solved from keyframes. It now means the RECORDING itself was
        // impossible (the element refused to screenshot), or that the caller turned
        // the path off with `videoSolveEnabled: false`. Either way there is nothing
        // left to try.
        if (e?.animated) {
          this.setState(CaptchaState.Animated);
          throw new Error(
            `Animated challenge could not be solved: ${e.message ?? 'recording failed'}`
          );
        }
        // Stage 2: we screenshotted a settled frame and the CLI says the puzzle
        // TYPE is unsupported (e.g. hCaptcha click/drag). Normally a definitive
        // verdict — fail fast. BUT if we've already interacted, a transient
        // blank/transition frame can still produce this; re-settle + retry once
        // before giving up (fixes the "solves round 1, dies on round 2" case).
        if (e?.unsupported) {
          if (hasInteracted && unsupportedRetries < maxUnsupportedRetries) {
            unsupportedRetries++;
            // Almost certainly a not-yet-settled next round. Actively wait for
            // it to settle before retrying; if it never settles it's animated.
            const el = await this.detectCaptcha(page);
            if (el) {
              const s = await this.waitForElementSettled(el);
              if (s === 'animated') {
                // Used to be terminal. Now it just means the next round is an
                // animated puzzle: retry the loop and solveSingle takes the
                // recording path. `unsupportedRetries` still bounds it, so a
                // widget that is animated AND unsolvable cannot spin here.
                if (this.config.videoSolveEnabled === false) {
                  this.setState(CaptchaState.Animated);
                  throw new Error(
                    'Animated/video challenge detected — the puzzle never settles '
                    + 'and videoSolveEnabled is off.'
                  );
                }
                console.log(
                  '"unsupported" mid-solve and the next round is animated; '
                  + 'retrying into the recording path.',
                );
                continue;
              }
            }
            console.log(
              `"unsupported" mid-solve (not-yet-settled next round); settled and `
              + `retrying (${unsupportedRetries}/${maxUnsupportedRetries}).`,
            );
            continue;
          }
          throw new Error(
            'Cannot solve this kind of captcha — the rendered puzzle is not a '
            + 'supported grid or checkbox (likely an hCaptcha click/drag puzzle).'
          );
        }
        // Stale/detached challenge handle: hCaptcha swapped in the next round
        // while we held the old iframe, so a screenshot on it fails "not
        // visible" / "Timeout" / "not attached". This is a transition, not a
        // dead puzzle — back off, then let the loop re-detect the fresh
        // challenge. (Only after we've interacted; a first-frame failure is a
        // genuine problem worth surfacing.)
        const emsg = String((e && (e as any).message) || e);
        if (
          hasInteracted
          && staleElementRetries < maxStaleElementRetries
          && /Timeout .*exceeded|not visible|not attached|detached|Target closed/i.test(emsg)
        ) {
          staleElementRetries++;
          console.log(
            `stale challenge handle after submit ("${emsg.split('\n')[0]}"); `
            + `re-detecting next round (${staleElementRetries}/${maxStaleElementRetries}).`,
          );
          await delay(this.config.staleElementBackoffMs ?? 900);
          continue;
        }
        throw e;
      }
      hasInteracted = hasInteracted || didInteract;
      renderWaits = 0;
      cumulativeTokenUsage.push(...tokenUsage);

      // After acting, poll for the vendor's SOLVED signal (anchor checkbox
      // checked / response token) for a short window before falling back to the
      // normal re-detect. hCaptcha keeps the challenge iframe visible for a
      // couple seconds while it verifies the final answer; without this, the
      // loop re-entered the pipeline on that closing frame and burned ~18s
      // (waitForHcaptchaChallengeImages timing out on a prompt-less frame).
      // This ONLY early-returns on a definitive solved signal — it never
      // re-solves, so it can't loop. If not solved in the window, the original
      // detectCaptcha path below handles a genuine next round exactly as before.
      const settleMs = didInteract
        ? (this.config.postSolveOutcomeTimeoutMs ?? 2500)
        : postSolveDelayMs + Math.random() * 300;
      if (didInteract) {
        const deadline = Date.now() + settleMs;
        let solved = false;
        while (Date.now() < deadline) {
          if (await this.isCaptchaSolved(page)) { solved = true; break; }
          // A fresh next round has rendered → stop waiting, go solve it now
          // (keeps multi-round solves fast instead of burning the full window).
          if (await this.isChallengeFreshlyRendered(page)) break;
          await delay(200);
        }
        if (solved) {
          return {
            isSolved: true,
            finalMousePosition: this.lastMousePosition,
            tokenUsage: aggregateTokenUsage(cumulativeTokenUsage),
          };
        }
      } else {
        await delay(settleMs);
      }

      // Detect reCAPTCHA's under-selection error banner. If present, the
      // vendor rejected our last submission because the model missed at
      // least one matching tile. Set the retry flag for the next loop so
      // the CLI augments the grid prompt with an explicit "you missed
      // some" instruction. If we've already retried once and the error is
      // STILL showing, bail — the model is stuck and the loop will just
      // keep flipping between "done" and Verify until timeout.
      const recaptchaUnderselect = await this.hasRecaptchaUnderselectError(page);
      if (recaptchaUnderselect) {
        if (alreadyRetriedRecaptchaError) {
          throw new Error(
            'reCAPTCHA still showing the under-selection error after retry; '
            + 'aborting (model unable to identify the missed tile). Total usage: '
            + JSON.stringify(aggregateTokenUsage(cumulativeTokenUsage))
          );
        }
        console.log('reCAPTCHA returned under-selection error; retrying with missed-tiles prompt.');
        pendingRetryMode = 'missed-tiles';
        alreadyRetriedRecaptchaError = true;
      }

      const after = await this.detectCaptcha(page);
      if (!after) {
        return {
          isSolved: true,
          finalMousePosition: this.lastMousePosition,
          tokenUsage: aggregateTokenUsage(cumulativeTokenUsage)
        };
      }

      // If we didn't actually interact and captcha is still detected, don't spin forever.
      if (!didInteract) {
        throw new Error(`Captcha still detected but solver performed no interactions; aborting to avoid an infinite loop. Total usage: ${JSON.stringify(aggregateTokenUsage(cumulativeTokenUsage))}`);
      }
    }

    throw new Error(`Captcha still detected after ${maxSolveLoops} solve loops. Total usage: ${JSON.stringify(aggregateTokenUsage(cumulativeTokenUsage))}`);
  }

  /**
   * Fire the optional onStep observer with a fresh screenshot of the captcha
   * element. No-op (beyond a cheap early return) when no callback is set, so it
   * stays off the critical path in normal runs. The emitted PNG is owned by the
   * callback — we never delete it. Best-effort: a screenshot or callback error
   * never fails the solve.
   */
  private async emitStep(
    captchaElement: ElementHandle,
    stage: SolveStepEvent['stage'],
    label: string,
    puzzleSource: SolveStepEvent['puzzleSource'],
    frameRole: SolveStepEvent['frameRole'],
    attempt: number,
    meta?: Record<string, any>,
  ): Promise<void> {
    const cb = this.config.onStep;
    if (!cb) return;
    this.stepIndex++;
    let screenshotPath: string | null = path.join(
      os.tmpdir(),
      `step_${this.stepIndex}_${Date.now()}_${Math.floor(Math.random() * 1e9)}.png`,
    );
    try {
      await captchaElement.screenshot({
        path: screenshotPath,
        timeout: this.config.elementScreenshotTimeoutMs ?? 8000,
        animations: 'disabled',
      });
    } catch {
      screenshotPath = null;
    }
    try {
      await cb({
        index: this.stepIndex,
        stage,
        label,
        screenshotPath,
        puzzleSource,
        frameRole,
        attempt,
        elapsedMs: this.solveStartMs ? Date.now() - this.solveStartMs : 0,
        meta,
      });
    } catch (e: any) {
      log(`onStep callback threw (ignored): ${e?.message ?? e}`);
    }
  }

  private async solveSingle(page: Page, captchaElement: ElementHandle, attempt: number, retryMode: string | null = null): Promise<{ didInteract: boolean, tokenUsage: TokenUsage[] }> {
    // Vendor hint helps the CLI route to the right pipeline (hCaptcha click
    // puzzles must never go through grid detection — find_grid false-positives
    // on the header+footer bands).
    const src = await captchaElement.getAttribute('src').catch(() => null);
    const puzzleSource = src && src.includes('hcaptcha.com')
      ? 'hcaptcha'
      : src && src.includes('recaptcha/api2')
        ? 'recaptcha'
        : 'unknown';

    // Distinguish the anchor "I'm not a robot" checkbox from the open image
    // challenge so recorders can drop the (useless) pre-challenge checkbox
    // screenshots and keep only the real puzzle. reCAPTCHA: anchor = api2/anchor,
    // challenge = api2/bframe. hCaptcha: anchor = frame=checkbox, challenge =
    // frame=challenge. (Note puzzleSource alone can't tell hCaptcha's checkbox
    // from its challenge — both srcs contain hcaptcha.com.)
    const frameRole: SolveStepEvent['frameRole'] =
      !src ? 'unknown'
        : src.includes('recaptcha/api2/bframe') || src.includes('frame=challenge')
          ? 'challenge'
          : src.includes('recaptcha/api2/anchor') || src.includes('frame=checkbox')
            ? 'checkbox'
            : 'unknown';

    // Everything the answer might have to be delivered INTO — a text box, a
    // slider handle — is looked up against this, never against the page. For
    // the iframed vendors it is the challenge document; for the ones that
    // render into the host page (GeeTest, Yidun, BotDetect, …) it is the widget
    // element, whose subtree is the same boundary.
    const scope: Frame | ElementHandle = (await captchaElement.contentFrame()) ?? captchaElement;

    // Does this puzzle want a STRING rather than a place to click? Only the DOM
    // can say. The picture cannot: BotDetect's warped code and hCaptcha's
    // "click the matching character" are the same genre of image and want
    // opposite answers. Restricted to `unknown` because neither hCaptcha nor
    // reCAPTCHA has ever served a typed challenge, so a match inside one of
    // their frames would be a false positive by definition.
    const textMode = puzzleSource === 'unknown'
      && (await this.findControl(scope, TEXT_INPUT_SELECTORS)) !== null;
    if (textMode) {
      console.log('Widget has a text box; solving as a distorted-text captcha.');
    }

    // hCaptcha swaps the challenge images in asynchronously — the iframe is
    // "visible" the instant the frame opens, but the task tiles paint a beat
    // later, and on multi-round puzzles it REUSES the same iframe: after a
    // submit it briefly shows the previous round, then a loading spinner, then
    // the next round. Screenshotting any of those transitional frames feeds the
    // model a blank/stale grid it correctly reports as "unsupported" — which
    // used to abort the whole solve on round 2. Gate on the challenge state:
    //   1. If we just submitted, wait for the frame to actually CHANGE (the
    //      transition starting) so we're past the previous round.
    //   2. Wait for the tiles to paint (DOM) AND for the pixels to stop moving
    //      (settle monitor). If it never settles, it's an animated/video puzzle.
    let isAnimated = false;
    if (puzzleSource === 'hcaptcha' && src && src.includes('frame=challenge')) {
      if (this.lastSubmitFrameHash) {
        this.setState(CaptchaState.Transitioning);
        await this.waitForChangeSince(captchaElement, this.lastSubmitFrameHash);
        this.lastSubmitFrameHash = null;
      }
      this.setState(CaptchaState.Loading);
      await this.waitForHcaptchaChallengeImages(captchaElement);
      const settle = await this.waitForElementSettled(captchaElement);
      if (settle === 'animated') {
        // A challenge that never settles is animated BY DESIGN — hCaptcha's
        // "select the odd animal" fades its sprites on independent cycles, and
        // "unique motion pattern" spins identical meshes. This used to end the
        // solve; it now routes to the recording path below.
        if (this.config.videoSolveEnabled === false) {
          const e: any = new Error(
            'ANIMATED_CHALLENGE: the challenge never settles and videoSolveEnabled is off.',
          );
          e.animated = true;
          throw e;
        }
        console.log('[animated] challenge never settles — recording it');
        isAnimated = true;
      }
      this.setState(CaptchaState.Ready);
    } else if (puzzleSource === 'unknown' && this.config.videoSolveEnabled !== false) {
      // Non-hCaptcha, non-reCAPTCHA widgets (GeeTest, Tencent, …). The settle probe
      // was never run for these, so an animated one — GeeTest's svg board cycles its
      // glyph set — was screenshotted mid-cycle and answered from whatever single
      // moment we happened to catch. reCAPTCHA is excluded on purpose: it has its own
      // readiness gate below, its grids are never animated, and a second probe would
      // only add latency to a path that already works.
      if (await this.waitForElementSettled(captchaElement) === 'animated') {
        console.log('[animated] challenge never settles — recording it');
        isAnimated = true;
      }
    }

    // Only the image-challenge frame (bframe) holds a grid. The anchor checkbox
    // (api2/anchor) has none — running the grid settle/detect on it just wastes
    // an 8s timeout + a find-grid subprocess before the checkbox click. Gate the
    // grid handling to the bframe.
    const isRecaptchaChallenge = puzzleSource === 'recaptcha'
      && !!src && src.includes('recaptcha/api2/bframe');

    // reCAPTCHA fades new tiles in over ~1s (initial load and the in-place
    // dynamic refresh after a click). Screenshotting mid-fade feeds the LoRA a
    // blank/partial grid. Poll until the grid's cells have settled before
    // grabbing the frame. Best-effort — falls through on timeout. The in-place
    // refresh re-enters solveSingle each loop, so this guard covers it too.
    // True only for a one-shot reCAPTCHA grid (4x4): click all matching tiles,
    // then submit in the same pass — these never blank/fade, so there's no
    // dynamic-refresh loop to run.
    let isRecaptchaOneShotGrid = false;
    // Grid size the solver establishes for this challenge, surfaced in the
    // baseline step's meta so callers (e.g. the demo recorder) can bucket
    // reCAPTCHA attempts into 3x3 vs 4x4 without scraping debug logs.
    let establishedGridSize: number | null = null;
    if (isRecaptchaChallenge) {
      await this.waitForGridCellsLoaded(captchaElement);
      // 3x3 reCAPTCHA puzzles refresh tiles in place (blank/fade → new image),
      // so they need the multi-round driver: click → hover/wait for fades →
      // re-solve, submitting only when the CLI says `done`. 4x4 puzzles only ever
      // return `checked` (no in-place refresh) and are one-shot like hCaptcha.
      // Falls through if the grid can't be established.
      const grid = await this.getGridBoxes(captchaElement);
      if (grid && grid.size === 3) {
        establishedGridSize = 3;
        const elementBox = await captchaElement.boundingBox();
        if (elementBox) {
          return this.solveRecaptchaGrid(page, captchaElement, attempt, retryMode, grid, elementBox);
        }
      } else if (grid && grid.size === 4) {
        establishedGridSize = 4;
        isRecaptchaOneShotGrid = true;
      }
    }

    // 1. Take Screenshot
    const screenshotPath = path.join(os.tmpdir(), `captcha_${Date.now()}_${Math.floor(Math.random() * 1e9)}.png`);
    await captchaElement.screenshot({
      path: screenshotPath,
      timeout: this.config.elementScreenshotTimeoutMs ?? 8000,
      animations: 'disabled',
    });

    // Save image to debug directory if debugging is enabled
    this.saveImageForDebug(screenshotPath);

    // Baseline screenshot before any action is taken. Emitted once per solve
    // (the first time we reach a one-shot/checkbox screenshot); later loops are
    // covered by the post-action 'submit'/'round' steps.
    if (this.stepIndex === 0) {
      await this.emitStep(captchaElement, 'initial', 'initial (pre-action)', puzzleSource, frameRole, attempt,
        establishedGridSize ? { gridSize: establishedGridSize } : undefined);
    }

    let performedAction = false;
    let slid = false;
    let placed = false;
    let clicked = false;
    let typed = false;
    let allTokenUsage: TokenUsage[] = [];
    let burstDir: string | null = null;

    try {
      // 2. Call CLI — while the model generates (the main idle window), drift
      //    the cursor over the challenge like a human weighing the options,
      //    instead of freezing it in place. Wrapped in the freshness guard: if
      //    the frame changes mid-inference (a tile fades in), the answer is for
      //    a stale frame, so we re-screenshot and re-solve on the developed one.
      let response: CliResponse;
      if (isAnimated) {
        burstDir = await this.recordKeyframeBurst(captchaElement);
        response = await this.withIdleWander(page, captchaElement, () =>
          this.getAnimatedSolution(burstDir as string));
      } else {
        response = await this.solveFrameFreshnessGuarded(
          captchaElement, screenshotPath,
          (imagePath) => this.withIdleWander(page, captchaElement, () =>
            this.getSolution(imagePath, puzzleSource, retryMode, textMode)),
        );
      }
      const actions = response.actions;
      allTokenUsage = response.token_usage;

      // Archive debug artifacts if enabled
      this.archiveLatestDebugRun(attempt, actions);

      // 3. Execute Actions
      const actionList = Array.isArray(actions) ? actions : [actions];

      // We need the element's bounding box to translate coordinates
      const elementBox = await captchaElement.boundingBox();
      if (!elementBox) {
        throw new Error('Could not get bounding box of captcha element');
      }

      console.log(`Executing ${actionList.length} actions.`);
      const frame = await captchaElement.contentFrame();
      let verifyButton: ElementHandle | null = null;

      for (const action of actionList) {
        if (action.action === 'click') {
          const c = action as ClickAction;
          // v2 emits `target_bounding_boxes` (plural). v1 fields kept as fallbacks.
          const bboxes: Array<[number, number, number, number]> = c.target_bounding_boxes
            ?? (c.target_bounding_box ? [c.target_bounding_box] : []);
          if (!bboxes.length && !c.target_coordinates) {
            console.warn('Click action has no bboxes or coordinates', c);
            continue;
          }
          if (bboxes.length) {
            for (const bbox of bboxes) {
              // On an animated challenge, hold each click until the widget is back
              // in the state the model answered about. Per-click, not once per
              // action: these puzzles keep cycling, so by the time click 2 comes
              // round the state has moved on again.
              if (c.await_keyframe) {
                await this.waitForKeyframe(captchaElement, c.await_keyframe, ...bboxCenter(bbox));
              }
              await this.executeClick(page, captchaElement, { ...c, target_bounding_box: bbox } as ClickAction, elementBox);
              await delay(Math.random() * 80 + 80);
            }
          } else {
            await this.executeClick(page, captchaElement, c, elementBox);
          }
          performedAction = true;
          clicked = true;
          await this.emitStep(captchaElement, 'click', `clicked ${bboxes.length || 1} target(s)`, puzzleSource, frameRole, attempt, { bboxes });
        } else if (action.action === 'drag' && !(action as DragAction).source_bounding_box) {
          // No source — a puzzle-piece slider. What you grab is not what has to
          // arrive, so this cannot go through executeDrag: pressing the gap the
          // model named and dragging from there picks up nothing at all.
          if (await this.executeSlide(page, captchaElement, scope, action as DragAction, elementBox)) {
            performedAction = true;
            slid = true;
            await this.emitStep(captchaElement, 'drag', 'slid the piece into the slot', puzzleSource, frameRole, attempt, { action });
          }
        } else if (action.action === 'drag') {
          const d = action as DragAction;
          // Wait on the SOURCE: the piece has to be there to be picked up. The
          // destination is not gated — by the time the mouse arrives the animation
          // has moved on regardless, and a drop is judged by where it lands, not by
          // what the slot looked like on pickup.
          if (d.await_keyframe && d.source_bounding_box) {
            await this.waitForKeyframe(captchaElement, d.await_keyframe, ...bboxCenter(d.source_bounding_box));
          }
          await this.executeDrag(page, captchaElement, action as any, elementBox);
          performedAction = true;
          placed = true;
          await this.emitStep(captchaElement, 'drag', 'drag', puzzleSource, frameRole, attempt, { action });
        } else if (action.action === 'type') {
          if (await this.executeType(page, scope, action as TypeAction)) {
            performedAction = true;
            typed = true;
            await this.emitStep(captchaElement, 'type', 'typed the code', puzzleSource, frameRole, attempt, { action });
          }
        } else if (action.action === 'wait') {
          if ((action as any).duration_ms > 0) {
            console.log(`Waiting for ${(action as any).duration_ms}ms as requested by CLI`);
            await delay((action as any).duration_ms);
            performedAction = true;
            await this.emitStep(captchaElement, 'wait', `waited ${(action as any).duration_ms}ms`, puzzleSource, frameRole, attempt, { action });
          }
        }
        // `scope` when there is no vendor iframe. Eight vendors render into the
        // HOST PAGE — GeeTest, Yidun, Tencent, Yandex, Lemin, Prosopo,
        // MTCaptcha, BotDetect — so `contentFrame()` is null for all of them
        // and the button was never even SEARCHED FOR, while the text box and
        // the slider handle it sits beside were both found through `scope`
        // above. Two containers for two halves of one interaction.
        //
        // `scope` is the widget container and getVerifyButton's xpaths are
        // RELATIVE, so the submit of the FORM the captcha guards is out of
        // reach by construction. The press itself is bounded by
        // `shouldClickSubmit` below, which is where that hazard belongs.
        const lookup = frame ?? (slid ? null : scope);
        if (lookup) {
          verifyButton = await this.getVerifyButton(lookup);
          if (verifyButton) {
            await this.move(page, verifyButton);
          }
        }
        // 'done' actions intentionally fall through to the Verify-button block below.
      }

      // Submit policy: press the widget's own submit control whenever we have
      // put an ANSWER into it — a selection, a placed piece, a typed code — or
      // when we had nothing to do and want the round to advance.
      //
      // Two exclusions, and they are the whole rule:
      //
      //   a completed SLIDE has already submitted. Letting go of the handle is
      //     the gesture these puzzles grade; none of them ships a Verify
      //     button, so anything the generic finder turns up afterwards belongs
      //     to the HOST page, and pressing it would submit the form the captcha
      //     guards while the verdict is still in flight.
      //   a round that only WAITED has answered nothing.
      //
      // hCaptcha and the reCAPTCHA 4x4 used to be named here as one-shot
      // special cases; they are ordinary click rounds and this covers them.
      // (reCAPTCHA 3x3 never reaches here — solveRecaptchaGrid owns its
      // fade-and-re-round rounds.)
      //
      // A click round used to be excluded, on the reasoning that these boards
      // re-round and a half-made selection spends the attempt. They do not: the
      // ones that grade themselves mid-selection draw no submit control, so
      // verifyButton is null and nothing is pressed either way. What the
      // exclusion bought was an extra model call per puzzle, asking a board we
      // had already answered correctly whether it was `done`.
      const answered = clicked || placed || typed;
      const shouldClickSubmit = !slid && (answered || !performedAction);
      if (shouldClickSubmit && verifyButton) {
        console.log(performedAction
          ? `Actions executed; clicking Verify to submit (${puzzleSource}).`
          : 'No active actions performed (empty or done). Checking for Verify/Next button...');
        await this.moveAndClick(page, verifyButton);
        // The press IS an interaction, and saying so is load-bearing: the
        // caller aborts a round that reports none, so submitting a `done`
        // answer and then reporting false re-arms the very guard this
        // satisfies — the puzzle is sent and the solve gives up on it one line
        // later, which is what `prosopo_grid_3x3` did.
        performedAction = true;
        await this.emitStep(captchaElement, 'submit', 'submitted (Verify/Next)', puzzleSource, frameRole, attempt);
        // Snapshot the frame at submit time so the NEXT attempt waits for the
        // real transition (next round loading / frame closing) before treating
        // whatever is on screen as a fresh puzzle. See the hCaptcha gate above.
        this.setState(CaptchaState.Submitting);
        this.lastSubmitFrameHash = await this.elementFrameHash(captchaElement);
      }
    } finally {
      // Cleanup
      if (fs.existsSync(screenshotPath)) {
        fs.unlinkSync(screenshotPath);
      }
      // Only now: the wait gate re-reads the keyframe PNGs (which live inside this
      // directory) on every poll, so removing it any earlier would break the click
      // it is gating.
      if (burstDir) {
        try { fs.rmSync(burstDir, { recursive: true, force: true }); } catch { /* best-effort */ }
      }
    }

    return { didInteract: performedAction, tokenUsage: allTokenUsage };
  }

  private async getVerifyButton(frame: Frame | ElementHandle): Promise<ElementHandle | null> {
    let submitted = false;

    // 1. Try generic button selectors by text
    //
    // `.//` — RELATIVE. `frame` is an ElementHandle whenever the widget is
    // markup on the host page rather than a vendor iframe (all eight inline
    // vendors), and a document-rooted `//button` does not resolve against an
    // element handle: the query returns nothing even with the button sitting
    // inside that very element. On a Frame the context node is the document,
    // where `.//` and `//` mean the same thing, so the vendor paths are
    // unaffected — and scoping is the point on an element, since a
    // document-rooted match would reach the host page's own form submit.
    const buttonTexts = ['Verify', 'Next', 'Submit', 'Skip'];
    for (const text of buttonTexts) {
      try {
        // Case-insensitive contains for text
        const btn = await frame.$(
          `xpath=.//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '${text.toLowerCase()}')] | .//div[@role="button" and contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '${text.toLowerCase()}')]`
        );
        if (btn && await btn.isVisible()) {
          return btn;
        }
      } catch (e) {
        // Ignore locator errors
      }
    }

    if (!submitted) {
      // 2. Try specific ID (Recaptcha)
      const recaptchaVerify = await frame.$('#recaptcha-verify-button');
      if (recaptchaVerify && await recaptchaVerify.isVisible()) {
        return recaptchaVerify;
      }
    }

    if (!submitted) {
      // 3. Try specific class (hCaptcha)
      const hcaptchaVerify = await frame.$('.button-submit');
      if (hcaptchaVerify && await hcaptchaVerify.isVisible()) {
        return hcaptchaVerify;
      }
    }
    return null;
  }

  private async hasNonEmptyFieldValue(page: Page, selector: string): Promise<boolean> {
    try {
      const el = await page.$(selector);
      if (!el) return false;
      const value = await page.$eval(selector, node => {
        const anyNode = node as any;
        return typeof anyNode.value === 'string' ? anyNode.value : '';
      });
      return typeof value === 'string' && value.trim().length > 0;
    } catch {
      return false;
    }
  }

  /**
   * Detect reCAPTCHA's "Please select all matching images" error banner
   * (and the related "Please try again" / "Please also check the new images"
   * variants). These appear in the bframe AFTER clicking Verify with an
   * incomplete selection. The tiles do NOT refresh on this error — without
   * special handling the LoRA sees the same image, returns "done" (because
   * to it everything matching IS selected), we click Verify again, and we
   * loop until the session times out. We use this signal to switch the next
   * grid call into "missed-tiles" retry mode.
   */
  private async hasRecaptchaUnderselectError(page: Page): Promise<boolean> {
    try {
      const bframe = await page.$('iframe[src*="recaptcha/api2/bframe"]');
      if (!bframe) return false;
      const frame = await bframe.contentFrame();
      if (!frame) return false;
      // Three selector variants reCAPTCHA uses for the same family of errors.
      const selectors = [
        '.rc-imageselect-error-select-more',
        '.rc-imageselect-error-dynamic-more',
        '.rc-imageselect-incorrect-response',
      ];
      for (const sel of selectors) {
        const el = await frame.$(sel);
        if (el) {
          // reCAPTCHA toggles these elements between visible / hidden via
          // an `aria-hidden` attribute on a wrapper — checking isVisible()
          // alone misses cases where the element is in the layout tree but
          // currently being faded in. Treat presence + non-empty text as
          // enough.
          const visible = await el.isVisible().catch(() => false);
          const text = (await el.textContent().catch(() => null)) ?? '';
          if (visible && text.trim().length > 0) return true;
        }
      }
      return false;
    } catch {
      return false;
    }
  }

  private async isRecaptchaAnchorChecked(anchorIframe: ElementHandle): Promise<boolean> {
    try {
      const frame = await anchorIframe.contentFrame();
      if (!frame) return false;
      const checked = await frame.$('.recaptcha-checkbox-checked');
      return !!(checked && await checked.isVisible());
    } catch {
      return false;
    }
  }

  private async isHcaptchaAnchorChecked(anchorIframe: ElementHandle): Promise<boolean> {
    // hCaptcha's anchor sets <div id="checkbox" aria-checked="true"> when
    // the puzzle has been solved. We use this as a solve signal because the
    // h-captcha-response token isn't always populated on demo pages.
    try {
      const frame = await anchorIframe.contentFrame();
      if (!frame) return false;
      const ariaChecked = await frame.$('#checkbox[aria-checked="true"]');
      return !!(ariaChecked && await ariaChecked.isVisible());
    } catch {
      return false;
    }
  }

  /**
   * True the moment the vendor reports the whole captcha solved — the anchor
   * checkbox flipped to checked, or the response token got populated. This is a
   * definitive "done" signal that a lingering, animating-closed challenge frame
   * is not: after the final submit, hCaptcha keeps the challenge iframe VISIBLE
   * for a couple of seconds while it verifies, so treating that frame as a fresh
   * puzzle (the old behavior) burned ~18s re-running the pipeline on it.
   */
  private async isCaptchaSolved(page: Page): Promise<boolean> {
    try {
      const hc = await page.$('iframe[src*="hcaptcha"][src*="frame=checkbox"]');
      if (hc && await hc.isVisible().catch(() => false)) {
        if (await this.hasNonEmptyFieldValue(page, '[name="h-captcha-response"]')) return true;
        if (await this.isHcaptchaAnchorChecked(hc)) return true;
      }
      const rc = await page.$('iframe[src*="recaptcha/api2/anchor"]');
      if (rc && await rc.isVisible().catch(() => false)) {
        if (await this.hasNonEmptyFieldValue(page, '[name="g-recaptcha-response"]')) return true;
        if (await this.isRecaptchaAnchorChecked(rc)) return true;
      }
    } catch { /* fall through */ }
    return false;
  }

  /**
   * True when an image challenge is open AND has actually rendered its prompt —
   * i.e. a fresh round we should solve, as opposed to a frame animating closed
   * (whose prompt has already gone). Used to tell "next round" from "solved,
   * closing" after a submit without waiting out a fixed timeout.
   */
  private async isChallengeFreshlyRendered(page: Page): Promise<boolean> {
    try {
      const hc = await page.$('iframe[src*="hcaptcha"][src*="frame=challenge"]');
      if (hc && await hc.isVisible().catch(() => false)) {
        const frame = await hc.contentFrame();
        const prompt = frame && await frame.$('.prompt-text');
        if (prompt && await prompt.isVisible().catch(() => false)) {
          const text = (await prompt.textContent().catch(() => '')) ?? '';
          if (text.trim().length > 0) return true;
        }
      }
      const rc = await page.$('iframe[src*="recaptcha/api2/bframe"]');
      if (rc && await rc.isVisible().catch(() => false)) {
        const frame = await rc.contentFrame();
        const instr = frame && await frame.$('.rc-imageselect-instructions, #rc-imageselect');
        if (instr && await instr.isVisible().catch(() => false)) return true;
      }
    } catch { /* fall through */ }
    return false;
  }


  /**
   * Block until the hCaptcha challenge frame's task images have actually
   * painted, so we don't screenshot a blank/half-loaded grid.
   *
   * hCaptcha renders each grid tile as a `.task-image .image` div whose
   * `background-image` is set once the asset loads; the prompt sits in
   * `.prompt-text`. Image-select (click/drag) challenges use a single
   * `.challenge-example` / `canvas` surface instead. We wait for either family
   * to be present AND for the background-image URLs to be populated (not the
   * empty `url("")` placeholder hCaptcha ships before the asset arrives).
   *
   * Best-effort: a timeout or a missing content frame just falls through to the
   * screenshot rather than throwing — the existing fail-fast path still covers a
   * genuinely unsupported puzzle.
   */
  private async waitForHcaptchaChallengeImages(challengeIframe: ElementHandle): Promise<void> {
    try {
      const frame = await challengeIframe.contentFrame();
      if (!frame) return;

      // Prompt must be present and non-empty first — it's the cheapest signal
      // that the challenge frame has rendered its content at all.
      await frame.waitForSelector('.prompt-text', { state: 'visible', timeout: 8000 });

      // Then wait for the actual imagery to load. Grid tiles expose a
      // background-image; click/drag puzzles expose a canvas or example image.
      await frame.waitForFunction(() => {
        const tiles = Array.from(
          document.querySelectorAll('.task-image .image, .task .image'),
        ) as HTMLElement[];
        if (tiles.length > 0) {
          // Every visible tile must have a real background-image URL.
          return tiles.every((el) => {
            const bg = getComputedStyle(el).backgroundImage;
            return bg && bg !== 'none' && !/url\(["']?["']?\)/.test(bg);
          });
        }
        // Non-grid (click/drag) challenge: a painted canvas or loaded example img.
        const canvas = document.querySelector('canvas');
        if (canvas instanceof HTMLCanvasElement && canvas.width > 0 && canvas.height > 0) {
          return true;
        }
        const example = document.querySelector(
          '.challenge-example img, .image-wrapper img',
        ) as HTMLImageElement | null;
        return !!(example && example.complete && example.naturalWidth > 0);
      }, { timeout: 8000 });
    } catch {
      // Timed out or frame detached mid-load; fall through to the screenshot.
    }
  }

  /**
   * Stage-1 detection: is an *interactive* captcha widget present in the DOM at
   * all — even if its iframe hasn't finished rendering yet?
   *
   * This is deliberately broader than detectCaptcha (which only returns a
   * VISIBLE, not-yet-solved element). We use it to distinguish two cases that
   * detectCaptcha() === null cannot tell apart:
   *
   *   - A reCAPTCHA-v2 / hCaptcha widget IS in the DOM but is still loading
   *     (iframe present, glyph not painted) → we should WAIT for it.
   *   - There is no interactive widget — reCAPTCHA v3 (score-based, invisible)
   *     or an hCaptcha that only triggers on a user action → we must FAIL FAST.
   *
   * reCAPTCHA v3 injects only `iframe[src*="recaptcha/api2/anchor"]` with
   * `size=invisible` in the src, and never an `api2/bframe` challenge frame, so
   * we exclude the invisible variant here.
   */
  public async hasInteractiveWidgetInDom(page: Page): Promise<boolean> {
    // reCAPTCHA v2 anchor, but NOT the invisible (v3 / invisible-v2) variant.
    const recaptchaAnchors = await page.$$('iframe[src*="recaptcha/api2/anchor"]');
    for (const a of recaptchaAnchors) {
      const src = (await a.getAttribute('src')) ?? '';
      if (!/[?&]size=invisible/.test(src)) return true;
    }
    // reCAPTCHA challenge frame present at all → definitely interactive.
    if (await page.$('iframe[src*="recaptcha/api2/bframe"]')) return true;

    // hCaptcha checkbox or challenge frame present (visible or not yet).
    if (await page.$('iframe[src*="hcaptcha"][src*="frame=checkbox"]')) return true;
    if (await page.$('iframe[src*="hcaptcha"][src*="frame=challenge"]')) return true;

    // The eight inline vendors, same table detectCaptcha uses. Without them
    // this answered "no widget" for every GeeTest / Yidun / Yandex / Lemin /
    // Prosopo / MTCaptcha / BotDetect / Tencent page whose puzzle had not
    // painted yet — and the caller reads that as "reCAPTCHA v3 / invisible"
    // and throws "No interactive captcha widget detected. Failing fast." in
    // under a second, instead of granting the render wait this method exists
    // to grant. The Python port has no such fail-fast, so the two ports
    // disagreed on every inline vendor (CLAUDE.md 1c) and Tier 3 scored it as
    // an unsolvable puzzle on the JS side only.
    //
    // Presence, not visibility — "in the DOM but not finished rendering" is
    // the entire question here; detectCaptcha still does the visibility check.
    for (const { selectors } of VENDOR_WIDGET_LOCATORS) {
      for (const selector of selectors) {
        if (await page.$(selector)) return true;
      }
    }

    return false;
  }

  public async detectCaptcha(page: Page): Promise<ElementHandle | null> {
    // Prioritize open challenges (the grid/images) over the initial checkbox

    // Recaptcha Challenge
    const recaptchaChallenge = await page.$('iframe[src*="recaptcha/api2/bframe"]');
    if (recaptchaChallenge && await recaptchaChallenge.isVisible()) return recaptchaChallenge;

    // hCaptcha Challenge — match the `frame=challenge` URL fragment.
    // The anchor iframe's title is "Widget containing checkbox for hCaptcha
    // security challenge" so a title-based fallback would mis-classify it
    // as the challenge frame. The URL fragment is unambiguous.
    const hcaptchaChallenge = await page.$('iframe[src*="hcaptcha"][src*="frame=challenge"]');
    if (hcaptchaChallenge && await hcaptchaChallenge.isVisible()) return hcaptchaChallenge;

    // Recaptcha Checkbox
    const recaptchaCheckbox = await page.$('iframe[src*="recaptcha/api2/anchor"]');
    if (recaptchaCheckbox && await recaptchaCheckbox.isVisible()) {
      // If it's already checked, consider it solved and continue searching.
      const checked = await this.isRecaptchaAnchorChecked(recaptchaCheckbox);
      if (!checked) return recaptchaCheckbox;
    }

    // hCaptcha Checkbox (anchor) — match the `frame=checkbox` URL fragment.
    const hcaptchaCheckbox = await page.$('iframe[src*="hcaptcha"][src*="frame=checkbox"]');
    if (hcaptchaCheckbox && await hcaptchaCheckbox.isVisible()) {
      // Solved if EITHER the h-captcha-response token is set OR the anchor
      // has flipped to aria-checked="true". Demo pages don't always populate
      // the token, so the visual state is the necessary tie-breaker.
      const hasToken = await this.hasNonEmptyFieldValue(page, '[name="h-captcha-response"]');
      const checked = await this.isHcaptchaAnchorChecked(hcaptchaCheckbox);
      if (!hasToken && !checked) return hcaptchaCheckbox;
    }

    // Cloudflare Turnstile
    // Try iframe first (if visible/open)
    const cloudflareIframe = await page.$('iframe[src*="challenges.cloudflare.com"]');
    if (cloudflareIframe && await cloudflareIframe.isVisible()) {
      const hasToken = await this.hasNonEmptyFieldValue(page, '[name="cf-turnstile-response"]');
      if (!hasToken) return cloudflareIframe;
    }

    // Fallback to container for closed shadow roots
    const cloudflareContainer = await page.$('.cf-turnstile');
    if (cloudflareContainer && await cloudflareContainer.isVisible()) {
      const hasToken = await this.hasNonEmptyFieldValue(page, '[name="cf-turnstile-response"]');
      if (!hasToken) return cloudflareContainer;
    }

    // Vendors with one interactive surface (no checkbox/challenge split) —
    // GeeTest, Tencent, Yidun, Yandex, Lemin, Prosopo, MTCaptcha, BotDetect.
    for (const { selectors } of VENDOR_WIDGET_LOCATORS) {
      for (const selector of selectors) {
        const el = await page.$(selector);
        if (el && await el.isVisible()) return el;
      }
    }

    return null;
  }

  /**
   * Initialize a fresh dump directory for one reCAPTCHA 3x3 dynamic-driver
   * session. Frames + a state.jsonl log land here so the click/fade/wait timing
   * can be replayed offline. Gated on CAPTCHA_DEBUG=1 — the per-frame dumps and
   * extra state queries add latency, so they stay off in normal runs. Set
   * CAPTCHA_DEBUG=1 to capture them when diagnosing timing. Best-effort.
   */
  private initGridDebug(): void {
    if (process.env.CAPTCHA_DEBUG !== '1') {
      this.gridDebugDir = null;
      return;
    }
    try {
      const cliRoot = this.config.repoPath ?? getBundledCliRoot();
      const base = path.join(cliRoot, 'latestDebugRun_grid');
      const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, -5);
      this.gridDebugDir = path.join(base, `griddrv_${stamp}_${Math.floor(Math.random() * 1e6)}`);
      this.gridDebugSeq = 0;
      fs.mkdirSync(this.gridDebugDir, { recursive: true });
      console.log(`[grid-debug] dumping driver frames + state to: ${this.gridDebugDir}`);
    } catch (e) {
      this.gridDebugDir = null;
      console.warn(`[grid-debug] could not init debug dir: ${e}`);
    }
  }

  /**
   * Log a structured event for the grid driver: prints a one-line summary to the
   * console and appends a JSON record to state.jsonl. If `framePath` is given,
   * copies that frame into the dump dir under a sequenced, labeled name so the
   * record can be matched to the exact pixels the detector saw. Best-effort.
   */
  private gridDebug(event: string, data: Record<string, any> = {}, framePath?: string): void {
    // No-op unless grid debugging is active (CAPTCHA_DEBUG=1). Keeps the verbose
    // per-poll trace + frame dumps off the hot path in normal runs.
    if (!this.gridDebugDir) return;
    const seq = ++this.gridDebugSeq;
    console.log(`[grid-debug #${seq}] ${event} ${JSON.stringify(data)}`);
    try {
      let savedFrame: string | undefined;
      if (framePath && fs.existsSync(framePath)) {
        savedFrame = `${String(seq).padStart(3, '0')}_${event}.png`;
        fs.copyFileSync(framePath, path.join(this.gridDebugDir, savedFrame));
      }
      const record = { seq, t: new Date().toISOString(), event, ...data, frame: savedFrame };
      fs.appendFileSync(path.join(this.gridDebugDir, 'state.jsonl'), JSON.stringify(record) + '\n');
    } catch {
      // best-effort; never fail the solve over debug I/O
    }
  }

  private saveImageForDebug(imagePath: string): void {
    // Check if CAPTCHA_DEBUG is enabled
    const debugEnabled = process.env.CAPTCHA_DEBUG === '1';
    if (!debugEnabled) {
      return;
    }

    try {
      const cliRoot = this.config.repoPath ?? getBundledCliRoot();
      // Save input images to a separate directory that won't be cleared by the Python CLI
      // The Python CLI clears latestDebugRun, so we use a sibling directory
      const inputImagesDir = path.join(cliRoot, 'latestDebugRun_inputs');

      // Ensure input images directory exists
      if (!fs.existsSync(inputImagesDir)) {
        fs.mkdirSync(inputImagesDir, { recursive: true });
      }

      // Increment counter and save with a descriptive name
      this.imageCounter++;
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, -5);
      const debugImageName = `input_${String(this.imageCounter).padStart(3, '0')}_${timestamp}.png`;
      const debugImagePath = path.join(inputImagesDir, debugImageName);

      // Copy the image to debug directory
      fs.copyFileSync(imagePath, debugImagePath);
      console.log(`[DEBUG] Saved input image to: ${debugImagePath}`);
    } catch (error) {
      // Don't fail the solve if debug save fails
      console.warn(`[DEBUG] Failed to save image for debugging: ${error}`);
    }
  }

  private archiveLatestDebugRun(attempt: number, actions: SolverResult): void {
    if (!this.sessionDebugDir) return;

    try {
      const cliRoot = this.config.repoPath ?? getBundledCliRoot();
      const latestDebugDir = path.join(cliRoot, 'latestDebugRun');
      const inputImagesDir = path.join(cliRoot, 'latestDebugRun_inputs');

      const attemptDir = path.join(this.sessionDebugDir, `attempt_${attempt}`);
      fs.mkdirSync(attemptDir, { recursive: true });

      // Archive CLI artifacts if they exist
      if (fs.existsSync(latestDebugDir)) {
        fs.cpSync(latestDebugDir, attemptDir, { recursive: true });
        fs.rmSync(latestDebugDir, { recursive: true, force: true });
      }

      // Archive input images if they exist
      if (fs.existsSync(inputImagesDir)) {
        const archivedInputsDir = path.join(attemptDir, 'inputs');
        fs.mkdirSync(archivedInputsDir, { recursive: true });
        fs.cpSync(inputImagesDir, archivedInputsDir, { recursive: true });
        fs.rmSync(inputImagesDir, { recursive: true, force: true });
      }

      // Add actions info to the attempt directory
      fs.writeFileSync(
        path.join(attemptDir, 'actions_result.json'),
        JSON.stringify(actions, null, 2)
      );

      console.log(`[DEBUG] Archived attempt ${attempt} debug artifacts to: ${attemptDir}`);
    } catch (error) {
      console.warn(`[DEBUG] Failed to archive debug artifacts: ${error}`);
    }
  }

  /**
   * Resolve the bundled CaptchaKraken CLI root and the python interpreter to
   * run it with. Prefers the packaged venv python (postinstall bootstrap),
   * falling back to the configured/`python` command. Throws if the CLI folder
   * is missing — callers that must not throw (e.g. runCliTool) wrap this.
   */
  private resolveCli(): { cliRoot: string; py: string } {
    const { repoPath, pythonCommand = 'python' } = this.config;
    const cliRoot = repoPath ?? getBundledCliRoot();
    if (!fs.existsSync(cliRoot)) {
      throw new Error(
        `CaptchaKraken CLI folder not found at ${cliRoot}. ` +
        `If you installed from npm, ensure the package ships 'python/'.`
      );
    }
    const py = getVenvPython(cliRoot) ?? pythonCommand;
    return { cliRoot, py };
  }

  /**
   * Run an OpenCV tool subcommand of the CLI (e.g. `grid-cell-states a.png
   * b.png`) and return its parsed single-line JSON. These subcommands print
   * exactly one JSON object on stdout (timing records go to stderr), so we
   * parse the whole trimmed stdout. Best-effort: returns `{}` on any failure so
   * polling callers can treat it as "inconclusive, keep going" without throwing.
   */
  private async runCliTool(args: string[]): Promise<any> {
    try {
      const { cliRoot, py } = this.resolveCli();
      // Use execFile (no shell) so args containing JSON / brackets / spaces —
      // e.g. the grid_boxes payload for grid-cell-states-fixed — are passed
      // literally without any shell quoting/globbing hazards.
      const { stdout } = await execFileAsync(py, ['-m', 'captchakraken.cli', ...args], {
        cwd: cliRoot,
        env: cliEnv(cliRoot),
        maxBuffer: 10 * 1024 * 1024,
      });
      return JSON.parse(stdout.trim());
    } catch {
      return {};
    }
  }

  /**
   * Lazily start the persistent CV worker (`python -m captchakraken.cli serve`) and resolve
   * once it has imported cv2/numpy and emitted its `{"ready":true}` handshake.
   * Returns false if it can't be started (caller then falls back to one-shot
   * subprocesses). Idempotent: subsequent calls await the same readiness promise.
   */
  private ensureCvWorker(): Promise<boolean> {
    if (this.cvWorkerReady) return this.cvWorkerReady;
    this.cvWorkerReady = new Promise<boolean>((resolve) => {
      try {
        const { cliRoot, py } = this.resolveCli();
        const proc = spawn(py, ['-m', 'captchakraken.cli', 'serve'], { cwd: cliRoot, env: cliEnv(cliRoot) });
        this.cvWorker = proc;

        let settled = false;
        const fail = () => {
          if (!settled) { settled = true; resolve(false); }
          this.teardownCvWorker();
        };

        proc.stdout.on('data', (chunk: Buffer) => {
          this.cvWorkerBuf += chunk.toString();
          let nl: number;
          while ((nl = this.cvWorkerBuf.indexOf('\n')) >= 0) {
            const line = this.cvWorkerBuf.slice(0, nl).trim();
            this.cvWorkerBuf = this.cvWorkerBuf.slice(nl + 1);
            if (!line) continue;
            let msg: any;
            try { msg = JSON.parse(line); } catch { continue; }
            if (!settled && msg.ready === true) { settled = true; resolve(true); continue; }
            if (typeof msg.id === 'number' && this.cvWorkerPending.has(msg.id)) {
              const p = this.cvWorkerPending.get(msg.id)!;
              this.cvWorkerPending.delete(msg.id);
              if (msg.ok) p.resolve(msg.result);
              else p.reject(new Error(msg.error || 'cv worker error'));
            }
          }
        });
        proc.on('error', fail);
        proc.on('exit', () => {
          // Reject any in-flight requests so callers fall back rather than hang.
          for (const [, p] of this.cvWorkerPending) p.reject(new Error('cv worker exited'));
          this.cvWorkerPending.clear();
          fail();
        });
        // Bounded readiness wait — if imports stall, fall back to one-shot.
        setTimeout(() => { if (!settled) { settled = true; resolve(false); } }, 8000);
      } catch {
        resolve(false);
      }
    });
    return this.cvWorkerReady;
  }

  /** Send one request to the CV worker and await its JSON result. Throws on any
   *  worker failure so callers can fall back to the one-shot path. */
  private cvWorkerRequest(payload: Record<string, any>, timeoutMs = 10000): Promise<any> {
    const proc = this.cvWorker;
    if (!proc || proc.exitCode !== null) return Promise.reject(new Error('cv worker not running'));
    const id = ++this.cvWorkerSeq;
    return new Promise<any>((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this.cvWorkerPending.delete(id)) reject(new Error('cv worker request timeout'));
      }, timeoutMs);
      this.cvWorkerPending.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      try {
        proc.stdin.write(JSON.stringify({ id, ...payload }) + '\n');
      } catch (e) {
        this.cvWorkerPending.delete(id);
        clearTimeout(timer);
        reject(e);
      }
    });
  }

  /** Kill the worker and clear state. Safe to call repeatedly. */
  private teardownCvWorker(): void {
    const proc = this.cvWorker;
    this.cvWorker = null;
    if (proc) { try { proc.kill(); } catch { /* best-effort */ } }
  }

  /**
   * Run a CV tool through the persistent worker when available, falling back to a
   * one-shot `runCliTool` subprocess otherwise. `cmd`/`payload` map to the
   * worker's protocol; `fallbackArgs` is the equivalent one-shot argv. Worker
   * results are wrapped to match the one-shot JSON shape:
   *   - grid-cell-states[-fixed]: worker returns the states object directly, or
   *     {grid:null}; the one-shot returns the same shape, so just pass through.
   *   - find-grid: worker returns the array (or null) as `result`.
   * Best-effort: never throws.
   */
  private async runCvTool(cmd: string, payload: Record<string, any>, fallbackArgs: string[]): Promise<any> {
    try {
      if (await this.ensureCvWorker()) {
        const result = await this.cvWorkerRequest({ cmd, ...payload });
        return result;
      }
    } catch {
      // fall through to one-shot
    }
    return this.runCliTool(fallbackArgs);
  }

  /**
   * Block until a reCAPTCHA grid's cells have settled — none blank, none
   * mid-fade — before we screenshot it for the model. reCAPTCHA fades new tiles
   * in over ~1s; capturing mid-fade feeds the LoRA a blank/partial grid.
   *
   * We poll: screenshot the challenge element, keep the last two frames, and ask
   * the CLI's batched `grid-cell-states` (one subprocess per poll) which cells
   * are empty/changing/loaded. We return as soon as every cell is loaded, or on
   * timeout. Best-effort, mirroring `waitForHcaptchaChallengeImages`: never
   * throws, and falls through on timeout so a stuck/odd grid still proceeds to
   * the normal screenshot path. Temp frames are always cleaned up.
   */
  private async waitForGridCellsLoaded(
    captchaElement: ElementHandle,
    opts?: { intervalMs?: number; timeoutMs?: number },
  ): Promise<boolean> {
    const interval = opts?.intervalMs ?? this.config.gridLoadPollIntervalMs ?? 250;
    const timeout = opts?.timeoutMs ?? this.config.gridLoadTimeoutMs ?? 8000;
    const start = Date.now();
    const frames: string[] = [];
    const tmp = () => path.join(
      os.tmpdir(),
      `gridpoll_${Date.now()}_${Math.floor(Math.random() * 1e9)}.png`,
    );
    try {
      while (Date.now() - start < timeout) {
        const f = tmp();
        await captchaElement.screenshot({ path: f });
        frames.push(f);

        if (frames.length >= 2) {
          const a = frames[frames.length - 2];
          const b = frames[frames.length - 1];
          const res = await this.runCvTool('grid-cell-states', { a, b }, ['grid-cell-states', a, b]);
          // `{grid: null}` => grid not painted yet; keep polling. A real grid
          // result with no empty/changing cells and >=1 loaded cell => settled.
          const gridFound = res && res.grid !== null && Array.isArray(res.loaded);
          if (
            gridFound
            && Array.isArray(res.empty) && res.empty.length === 0
            && Array.isArray(res.changing) && res.changing.length === 0
            && res.loaded.length > 0
          ) {
            return true;
          }
          // Drop the older frame so disk use stays bounded to one prior frame.
          const stale = frames.shift();
          if (stale && fs.existsSync(stale)) fs.unlinkSync(stale);
        }

        await delay(interval);
      }
      return false;
    } catch {
      return false;
    } finally {
      for (const f of frames) {
        if (fs.existsSync(f)) {
          try { fs.unlinkSync(f); } catch { /* best-effort cleanup */ }
        }
      }
    }
  }

  /**
   * Read a PNG's pixel dimensions from its IHDR chunk (bytes 16-23, big-endian).
   * Avoids pulling in an image-size dependency. Returns null if the file isn't a
   * readable PNG.
   */
  private readPngDimensions(filePath: string): { width: number; height: number } | null {
    try {
      const fd = fs.openSync(filePath, 'r');
      try {
        const buf = new Uint8Array(24);
        const read = fs.readSync(fd, buf, 0, 24, 0);
        if (read < 24) return null;
        // PNG signature is 8 bytes; IHDR length+type is 8 more; then width/height
        // as big-endian uint32s at byte offsets 16 and 20.
        const isPng = buf[1] === 0x50 && buf[2] === 0x4e && buf[3] === 0x47; // "PNG"
        if (!isPng) return null;
        const beU32 = (o: number) => (buf[o] << 24 | buf[o + 1] << 16 | buf[o + 2] << 8 | buf[o + 3]) >>> 0;
        const width = beU32(16);
        const height = beU32(20);
        if (!width || !height) return null;
        return { width, height };
      } finally {
        fs.closeSync(fd);
      }
    } catch {
      return null;
    }
  }

  /**
   * Detect the reCAPTCHA grid once for a puzzle session: screenshot the element,
   * run `find-grid`, and read the screenshot's pixel dimensions. Grid boxes are
   * pixel coords in SCREENSHOT space (not page CSS space). Returns null if no
   * grid is detected. The geometry is stable across the in-place dynamic refresh
   * (only tile images change), so callers cache the result for the session.
   */
  private async getGridBoxes(
    captchaElement: ElementHandle,
  ): Promise<{ boxes: number[][]; size: 3 | 4; screenshotW: number; screenshotH: number } | null> {
    const f = path.join(os.tmpdir(), `findgrid_${Date.now()}_${Math.floor(Math.random() * 1e9)}.png`);
    try {
      await captchaElement.screenshot({ path: f });
      const res = await this.runCvTool('find-grid', { image: f }, ['find-grid', f]);
      if (!Array.isArray(res) || (res.length !== 9 && res.length !== 16)) {
        return null;
      }
      const dims = this.readPngDimensions(f);
      if (!dims) return null;
      return {
        boxes: res as number[][],
        size: res.length === 16 ? 4 : 3,
        screenshotW: dims.width,
        screenshotH: dims.height,
      };
    } catch {
      return null;
    } finally {
      if (fs.existsSync(f)) {
        try { fs.unlinkSync(f); } catch { /* best-effort cleanup */ }
      }
    }
  }

  /**
   * Map a model-returned normalized bbox (fractions of the element/screenshot)
   * to a 1-indexed grid cell. Uses the bbox center, converts to screenshot
   * pixels, and returns the cell whose pixel box contains it. Cell numbering is
   * row-major (matches the CLI's find_grid output). Returns null if the center
   * falls outside every cell (e.g. in a gutter) — callers click the raw bbox
   * anyway and skip per-tile tracking.
   */
  private bboxToCell(
    bbox: [number, number, number, number],
    gridBoxes: number[][],
    screenshotW: number,
    screenshotH: number,
  ): number | null {
    const [x1, y1, x2, y2] = bbox;
    const cx = ((x1 + x2) / 2) * screenshotW;
    const cy = ((y1 + y2) / 2) * screenshotH;
    for (let i = 0; i < gridBoxes.length; i++) {
      const [bx1, by1, bx2, by2] = gridBoxes[i];
      if (cx >= bx1 && cx <= bx2 && cy >= by1 && cy <= by2) {
        return i + 1; // 1-indexed
      }
    }
    return null;
  }

  /**
   * Center of a 1-indexed grid cell in PAGE pixel space, for mouse moves.
   * Converts the cached screenshot-pixel box to page coords via the session's
   * scaleX/scaleY (screenshot px -> page px) and element origin.
   */
  private cellCenterPage(cell: number, session: GridSession): { x: number; y: number } {
    const [x1, y1, x2, y2] = session.gridBoxes[cell - 1];
    const cxPx = (x1 + x2) / 2;
    const cyPx = (y1 + y2) / 2;
    return {
      x: session.elementBox.x + cxPx * session.scaleX,
      y: session.elementBox.y + cyPx * session.scaleY,
    };
  }

  /** Smooth-move the mouse over one cell's center with intra-cell jitter. */
  private async hoverCell(page: Page, session: GridSession, cell: number): Promise<void> {
    const cellWPage = (session.gridBoxes[0][2] - session.gridBoxes[0][0]) * session.scaleX;
    const cellHPage = (session.gridBoxes[0][3] - session.gridBoxes[0][1]) * session.scaleY;
    const center = this.cellCenterPage(cell, session);
    const jitterX = (Math.random() - 0.5) * cellWPage * 0.4;
    const jitterY = (Math.random() - 0.5) * cellHPage * 0.4;
    await this.performSmoothMove(page, center.x + jitterX, center.y + jitterY);
  }

  /**
   * Query per-cell grid state using the SESSION'S CACHED grid boxes via the
   * `grid-cell-states-fixed` CLI command. This is critical: the dynamic refresh
   * blanks tiles to near-white, which makes find_grid fail on that frame, so the
   * self-detecting `grid-cell-states` would return {grid:null} mid-fade and a
   * naive caller would misread that as "nothing loading / solved". Passing the
   * cached boxes keeps empty/changing/selected correct even while tiles are
   * blank. Returns null only on a genuine CLI failure. Best-effort.
   */
  private async gridCellStates(
    session: GridSession,
    frameA: string,
    frameB: string,
  ): Promise<GridCellStates | null> {
    const boxesJson = JSON.stringify(session.gridBoxes);
    const res = await this.runCvTool(
      'grid-cell-states-fixed',
      { a: frameA, b: frameB, grid_boxes: session.gridBoxes },
      ['grid-cell-states-fixed', frameA, frameB, boxesJson],
    );
    if (!res || !Array.isArray(res.empty)) return null;
    return {
      empty: res.empty ?? [],
      changing: res.changing ?? [],
      loaded: res.loaded ?? [],
      selected: res.selected ?? [],
    };
  }

  /** Order a loading set so `priority` cells (just-clicked) come first. */
  private orderByPriority(loading: number[], priority: number[]): number[] {
    const set = new Set(loading);
    const ordered: number[] = [];
    for (const c of priority) {
      if (set.has(c)) { ordered.push(c); set.delete(c); }
    }
    for (const c of set) ordered.push(c);
    return ordered;
  }

  /**
   * Detect whether any tiles are blank or fading, watching for the ONSET of the
   * reCAPTCHA refresh over a short grace window. The blank/fade transition lags
   * the click by a beat, so a single snapshot right after clicking misses it
   * (the tile still shows its old image — not yet white, not yet changing). We
   * poll consecutive frames and mark a cell loading if it is `empty` (≥97%
   * near-white) OR `changing` (>2% pixels differ). HOVERS a clicked tile each
   * poll so the mouse keeps moving (no unnatural pauses). Returns the loading
   * cells (priority/clicked first) as soon as any appears, or [] if the whole
   * window passes with nothing loading (→ solved). Logs every poll + frame.
   */
  private async currentLoadingCells(
    page: Page,
    captchaElement: ElementHandle,
    session: GridSession,
    priority: number[] = [],
  ): Promise<number[]> {
    const grace = this.config.recaptchaFadeOnsetGraceMs ?? 4000;
    const interval = this.config.recaptchaDynamicFadePollMs ?? 250;
    const start = Date.now();
    const frames: string[] = [];
    const tmp = () => path.join(os.tmpdir(), `loadchk_${Date.now()}_${Math.floor(Math.random() * 1e9)}.png`);
    // We care specifically about the tiles we just clicked (priority). reCAPTCHA
    // holds them selected (old image visible) for ~1-3s, THEN blanks them to swap
    // in a replacement. So we must watch the CLICKED cells across the whole grace
    // window — the onset is delayed, not immediate.
    const watch = priority.length ? priority : null; // null => watch all cells
    this.gridDebug('fade-onset:start', { grace, priority, watching: watch ?? 'all' });
    let hoverIdx = 0;
    try {
      const first = tmp();
      await captchaElement.screenshot({ path: first });
      frames.push(first);
      this.gridDebug('fade-onset:baseline', {}, first);

      let polls = 0;
      while (Date.now() - start < grace) {
        // Keep the mouse moving over a clicked tile during the wait, and enforce
        // a minimum inter-frame gap so the change detector has a real diff (the
        // worker query is near-instant, so without this polls could fire back-to-
        // back on near-identical frames and miss a slow fade).
        const iterStart = Date.now();
        if (priority.length) {
          await this.hoverCell(page, session, priority[hoverIdx % priority.length]).catch(() => {});
          hoverIdx++;
        }
        const elapsed = Date.now() - iterStart;
        if (elapsed < interval) await delay(interval - elapsed);
        const f = tmp();
        await captchaElement.screenshot({ path: f });
        frames.push(f);
        polls++;

        const a = frames[frames.length - 2];
        const b = frames[frames.length - 1];
        const st = await this.gridCellStates(session, a, b);
        // Restrict the loading signal to the cells we clicked (if known): a
        // background tile changing is irrelevant; a clicked tile going blank/
        // changing means the refresh has begun.
        const inScope = (c: number) => !watch || watch.includes(c);
        const emptyW = (st?.empty ?? []).filter(inScope);
        const changingW = (st?.changing ?? []).filter(inScope);
        this.gridDebug('fade-onset:poll', {
          poll: polls, elapsedMs: Date.now() - start,
          watchedEmpty: emptyW, watchedChanging: changingW,
          empty: st?.empty ?? null, changing: st?.changing ?? null,
          loaded: st?.loaded ?? null, selected: st?.selected ?? null,
        }, b);
        const loading = [...new Set([...emptyW, ...changingW])];
        if (loading.length) {
          const ordered = this.orderByPriority(loading, priority);
          this.gridDebug('fade-onset:loading-detected', { loading: ordered, afterMs: Date.now() - start });
          return ordered;
        }

        const stale = frames.shift();
        if (stale && fs.existsSync(stale)) fs.unlinkSync(stale);
      }
      this.gridDebug('fade-onset:none', { afterMs: Date.now() - start, polls });
      return [];
    } catch (e) {
      this.gridDebug('fade-onset:error', { error: String(e) });
      return [];
    } finally {
      for (const f of frames) {
        if (fs.existsSync(f)) { try { fs.unlinkSync(f); } catch { /* best-effort */ } }
      }
    }
  }

  /**
   * After loading is detected, wait until at least one of the given blank/fading
   * cells reaches the `loaded` state, HOVERING those cells (in order) the whole
   * time so the mouse never sits still. Returns true once a tile loads, false on
   * timeout (caller proceeds anyway). Uses the session's cached grid boxes so it
   * works while tiles are blank. Logs every poll + frame.
   */
  private async waitForAnyClickedTileLoaded(
    page: Page,
    captchaElement: ElementHandle,
    session: GridSession,
    fadingCells: number[],
  ): Promise<boolean> {
    if (!fadingCells.length) return true;
    const interval = this.config.recaptchaDynamicFadePollMs ?? 250;
    const timeout = this.config.recaptchaDynamicFadeWaitMs ?? 6000;
    const hoverEnabled = this.config.recaptchaTileHoverEnabled ?? true;
    const start = Date.now();
    const frames: string[] = [];
    const tmp = () => path.join(os.tmpdir(), `fadepoll_${Date.now()}_${Math.floor(Math.random() * 1e9)}.png`);
    this.gridDebug('wait-load:start', { fadingCells, timeout, interval });
    let hoverIdx = 0;
    let polls = 0;
    try {
      while (Date.now() - start < timeout) {
        // Move over a fading tile each iteration — human waiting for the image.
        // Always enforce a minimum inter-frame gap so the change detector has a
        // real diff to work with even when the (now near-instant) worker query
        // would otherwise let polls fire back-to-back.
        const iterStart = Date.now();
        if (hoverEnabled) {
          await this.hoverCell(page, session, fadingCells[hoverIdx % fadingCells.length]).catch(() => {});
          hoverIdx++;
        }
        const elapsed = Date.now() - iterStart;
        if (elapsed < interval) await delay(interval - elapsed);
        const f = tmp();
        await captchaElement.screenshot({ path: f });
        frames.push(f);

        if (frames.length >= 2) {
          const a = frames[frames.length - 2];
          const b = frames[frames.length - 1];
          const st = await this.gridCellStates(session, a, b);
          polls++;
          const loadedNow = st ? fadingCells.filter(c => st.loaded.includes(c)) : [];
          this.gridDebug('wait-load:poll', {
            poll: polls, elapsedMs: Date.now() - start,
            empty: st?.empty ?? null, changing: st?.changing ?? null,
            loaded: st?.loaded ?? null, loadedTargets: loadedNow,
          }, b);
          if (loadedNow.length) {
            this.gridDebug('wait-load:loaded', { loadedNow, afterMs: Date.now() - start });
            return true;
          }
          const stale = frames.shift();
          if (stale && fs.existsSync(stale)) fs.unlinkSync(stale);
        }
      }
      this.gridDebug('wait-load:timeout', { afterMs: Date.now() - start, polls });
      return false;
    } catch (e) {
      this.gridDebug('wait-load:error', { error: String(e) });
      return false;
    } finally {
      for (const f of frames) {
        if (fs.existsSync(f)) { try { fs.unlinkSync(f); } catch { /* best-effort */ } }
      }
    }
  }

  /**
   * Multi-round driver for reCAPTCHA 3x3 dynamic puzzles ("click all X" where
   * tiles refresh in place). One invocation = one puzzle session.
   *
   * The CLI is authoritative about WHAT to do — it runs the blue-badge detector,
   * filters out already-selected and still-loading tiles, and returns one of:
   *   - `click`: click these tiles (already filtered to fresh, ready tiles)
   *   - `wait` : nothing to click yet, tiles are still loading — do NOT submit
   *   - `done` : nothing matching remains — submit (click Verify)
   *
   * This driver owns the HUMAN-LIKE WAITING the CLI can't: after a click round,
   * and on a `wait`, it hovers the just-clicked / currently blank+fading tiles
   * (in click order) and waits for at least one to finish reloading before
   * re-screenshotting and re-solving — so we don't burn a solver call on a grid
   * that's still mid-fade. It submits only on `done`.
   *
   * Returns the same shape as solveSingle so the outer solve loop — including the
   * under-selection retry and post-solve detectCaptcha — wraps it unchanged.
   */
  private async solveRecaptchaGrid(
    page: Page,
    captchaElement: ElementHandle,
    attempt: number,
    retryMode: string | null,
    grid: { boxes: number[][]; size: 3 | 4; screenshotW: number; screenshotH: number },
    elementBox: { x: number; y: number; width: number; height: number },
  ): Promise<{ didInteract: boolean; tokenUsage: TokenUsage[] }> {
    const maxRounds =
      this.config.recaptchaMaxDynamicRounds ?? DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS;

    const session: GridSession = {
      gridBoxes: grid.boxes,
      elementBox,
      scaleX: elementBox.width / grid.screenshotW,
      scaleY: elementBox.height / grid.screenshotH,
      screenshotW: grid.screenshotW,
      screenshotH: grid.screenshotH,
    };

    const clickedOrder: number[] = [];
    let performedAction = false;
    let shouldSubmit = false;
    const allTokenUsage: TokenUsage[] = [];
    let pendingRetry = retryMode;

    this.initGridDebug();
    this.gridDebug('session:init', {
      attempt, retryMode, size: grid.size,
      screenshotW: grid.screenshotW, screenshotH: grid.screenshotH,
      scaleX: session.scaleX, scaleY: session.scaleY,
      elementBox, gridBoxes: session.gridBoxes,
    });

    for (let round = 1; round <= maxRounds; round++) {
      // 1. Settle and screenshot.
      await this.waitForGridCellsLoaded(captchaElement);
      const shotA = path.join(os.tmpdir(), `recap_${Date.now()}_${Math.floor(Math.random() * 1e9)}.png`);
      await captchaElement.screenshot({ path: shotA });
      this.saveImageForDebug(shotA);
      // Per-round boundary snapshot for onStep observers. Round 1's snapshot is
      // the baseline (pre-action) for the 3x3 dynamic path.
      await this.emitStep(captchaElement, round === 1 ? 'initial' : 'round', `round-${round}:pre-solve`, 'recaptcha', 'challenge', attempt, { round });
      // Log the grid state the model is about to see — diagnostic only, so we
      // skip the extra state query unless grid debugging is active (keeps it off
      // the critical path in normal runs).
      if (this.gridDebugDir) {
        const preState = await this.gridCellStates(session, shotA, shotA);
        this.gridDebug(`round-${round}:pre-solve`, {
          round, pendingRetry,
          empty: preState?.empty ?? null, changing: preState?.changing ?? null,
          loaded: preState?.loaded ?? null, selected: preState?.selected ?? null,
          clickedOrder: [...clickedOrder],
        }, shotA);
      }

      let action: CaptchaAction | null = null;
      try {
        // 2. Solve. The CLI returns a single action for grid puzzles. Guarded
        //    against a mid-inference tile fade: if the grid changes while the
        //    model generates, re-screenshot and re-solve on the developed frame
        //    (the dynamic 3x3 puzzle is the case this matters most for).
        const retryForThisRound = pendingRetry;
        pendingRetry = null; // only the first round carries the inbound retry hint
        const response = await this.solveFrameFreshnessGuarded(
          captchaElement, shotA,
          (imagePath) => this.getSolution(imagePath, 'recaptcha', retryForThisRound),
        );
        this.archiveLatestDebugRun(attempt, response.actions);
        allTokenUsage.push(...response.token_usage);
        const actionList = Array.isArray(response.actions) ? response.actions : [response.actions];
        action = actionList[0] ?? null;
        this.gridDebug(`round-${round}:action`, { action });
      } finally {
        if (fs.existsSync(shotA)) {
          try { fs.unlinkSync(shotA); } catch { /* best-effort cleanup */ }
        }
      }

      // 3. Dispatch on the action type.
      if (!action || action.action === 'done') {
        // Nothing matching remains → submit.
        console.log(`[recaptcha-grid] round ${round}: done; submitting.`);
        this.gridDebug(`round-${round}:done`, {});
        shouldSubmit = true;
        break;
      }

      if (action.action === 'wait') {
        // Tiles are still loading; the CLI explicitly told us NOT to submit.
        // Find what's loading, hover it, and wait for at least one to settle.
        console.log(`[recaptcha-grid] round ${round}: CLI says wait (${(action as any).duration_ms ?? 0}ms).`);
        const loadingCells = await this.currentLoadingCells(page, captchaElement, session, clickedOrder);
        await this.waitForAnyClickedTileLoaded(page, captchaElement, session, loadingCells);
        continue;
      }

      if (action.action === 'click') {
        const c = action as ClickAction;
        const bboxes = c.target_bounding_boxes
          ?? (c.target_bounding_box ? [c.target_bounding_box] : []);
        if (!bboxes.length) {
          // Malformed click with no targets — treat as a soft wait so we don't
          // submit prematurely; re-solve next round.
          console.warn(`[recaptcha-grid] round ${round}: click action with no bboxes; re-solving.`);
          this.gridDebug(`round-${round}:click-no-bboxes`, {});
          await delay(500);
          continue;
        }

        // 4. Click the tiles in order, tracking cell numbers for hover ordering.
        const clickedThisRound: number[] = [];
        for (const bbox of bboxes) {
          const cell = this.bboxToCell(bbox, session.gridBoxes, session.screenshotW, session.screenshotH);
          await this.executeClick(page, captchaElement, { action: 'click', target_bounding_box: bbox } as ClickAction, elementBox);
          if (cell != null) {
            clickedOrder.push(cell);
            clickedThisRound.push(cell);
          }
          await delay(Math.random() * 80 + 80);
        }
        performedAction = true;
        console.log(`[recaptcha-grid] round ${round}: clicked ${bboxes.length} tile(s) -> cells ${JSON.stringify(clickedThisRound)}.`);
        this.gridDebug(`round-${round}:clicked`, { bboxes, clickedThisRound });
        await this.emitStep(captchaElement, 'click', `round-${round}:clicked ${bboxes.length} tile(s)`, 'recaptcha', 'challenge', attempt, { round, clickedThisRound, bboxes });

        // 5. The clicked tiles may go blank / fade out for a replacement
        //    (dynamic puzzle), or they may just stay checked (the puzzle is
        //    fully solved). reCAPTCHA's blank/fade transition lags the click, so
        //    we watch a grace window (not a single instant-after snapshot).
        const loadingCells = await this.currentLoadingCells(page, captchaElement, session, clickedThisRound);
        if (!loadingCells.length) {
          // Nothing is loading/fading within the grace window → the model fully
          // solved it; submit immediately rather than burning another round.
          console.log(`[recaptcha-grid] round ${round}: no tiles loading after click; submitting.`);
          this.gridDebug(`round-${round}:no-loading-submit`, {});
          shouldSubmit = true;
          break;
        }
        // Tiles are reloading — wait (while hovering) for at least one to settle
        // before re-solving, so we don't feed the model a mid-fade grid.
        console.log(`[recaptcha-grid] round ${round}: tiles loading ${JSON.stringify(loadingCells)}; waiting.`);
        await this.waitForAnyClickedTileLoaded(page, captchaElement, session, loadingCells);
        continue;
      }

      // Unexpected action type for a grid (drag/type) — re-solve.
      console.warn(`[recaptcha-grid] round ${round}: unexpected action '${(action as any).action}'; re-solving.`);
      this.gridDebug(`round-${round}:unexpected-action`, { action });
    }

    // Submit: click Verify if present (no-op if the grid is gone). Only when the
    // CLI signalled `done` — never on a timeout/round-cap exit, which leaves the
    // outer loop to re-detect and decide.
    if (shouldSubmit) {
      const frame = await captchaElement.contentFrame();
      if (frame) {
        const verifyButton = await this.getVerifyButton(frame);
        if (verifyButton) {
          console.log('[recaptcha-grid] clicking Verify to submit.');
          await this.moveAndClick(page, verifyButton);
          await this.emitStep(captchaElement, 'submit', 'submitted (Verify)', 'recaptcha', 'challenge', attempt);
        }
      }
    }

    return { didInteract: performedAction, tokenUsage: allTokenUsage };
  }

  /**
   * Record the animated challenge and return the directory holding the burst.
   *
   * The driver owns the browser, so it does the recording; the CLI does the
   * slicing (`solve-animated`). One zero-padded PNG per frame, because the slicer
   * sorts by name and reads the clip's temporal structure — `frame_9.png` sorting
   * after `frame_10.png` would shuffle the burst and turn a detectable cycle into
   * noise.
   *
   * Geometry comes from config and defaults to the collector's (4s @ 10fps), so a
   * challenge recorded here is the same shape of artifact the model trained on.
   * The caller must remove the directory when the actions are done with — the
   * keyframes the wait gate re-reads on every poll live inside it.
   */
  private async recordKeyframeBurst(captchaElement: ElementHandle): Promise<string> {
    const fps = Math.max(1, this.config.videoBurstFps ?? 10);
    const durationMs = this.config.videoBurstDurationMs ?? 4000;
    const total = Math.max(1, Math.round(durationMs / (1000 / fps)));
    const intervalMs = 1000 / fps;

    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ck_burst_'));
    let captured = 0;
    for (let i = 0; i < total; i++) {
      const started = Date.now();
      const frame = path.join(dir, `frame_${String(i).padStart(4, '0')}.png`);
      try {
        await captchaElement.screenshot({
          path: frame,
          timeout: this.config.elementScreenshotTimeoutMs ?? 8000,
          animations: 'disabled',
        });
        captured++;
      } catch {
        // A dropped frame costs a sample, not the recording.
      }
      // Drift-corrected: a slow screenshot must not stretch the clip, or the
      // burst covers more wall-clock than the model trained on and a cycle's
      // period lands differently across the frames.
      const wait = intervalMs - (Date.now() - started);
      if (wait > 0 && i < total - 1) await delay(wait);
    }
    if (!captured) {
      fs.rmSync(dir, { recursive: true, force: true });
      const e: any = new Error(
        'ANIMATED_CHALLENGE: could not record the animated challenge (no frame screenshotted).',
      );
      e.animated = true;
      throw e;
    }
    console.log(`[animated] recorded ${captured} frames at ${fps}fps -> ${dir}`);
    return dir;
  }

  /**
   * Slice a recorded burst into keyframes and solve them in ONE model request.
   *
   * Deliberately not routed through `solveFrameFreshnessGuarded`. That guard
   * re-solves when the frame changes during inference, and an animated challenge
   * changes by definition — every attempt would be judged stale and the whole
   * re-solve budget would burn without ever acting. The `frame` in the answer is
   * the real guard: it names the state to act in, and `waitForKeyframe` enforces it.
   *
   * Also not deduped by screenshot hash: there is no single screenshot, and two
   * recordings of the same widget are never byte-identical anyway.
   */
  private async getAnimatedSolution(framesDir: string): Promise<CliResponse> {
    const {
      model = process.env.CAPTCHA_LORA_NAME ?? 'captcha',
      apiKey = process.env.CAPTCHA_KRAKEN_API_KEY ?? process.env.VLLM_API_KEY,
    } = this.config;
    const { cliRoot, py } = this.resolveCli();

    const args = [
      '-m', 'captchakraken.cli', 'solve-animated',
      '--frames-dir', framesDir,
      '--fps', String(this.config.videoBurstFps ?? 10),
      '--model', model,
    ];
    if (apiKey) args.push('--api-key', apiKey);

    try {
      // execFile (no shell): the temp dir path is ours but still goes through
      // literally, with no quoting hazard.
      const { stdout, stderr } = await execFileAsync(py, args, {
        cwd: cliRoot,
        env: cliEnv(cliRoot, this.solveSessionId ? { CAPTCHA_KRAKEN_SESSION: this.solveSessionId } : undefined),
        maxBuffer: 10 * 1024 * 1024,
      });
      if (stderr) console.error('CaptchaKraken CLI stderr:', stderr);
      const parsed = JSON.parse(stdout.trim());
      console.log(
        `[animated] ${parsed.source_frames} frames -> ${(parsed.keyframes ?? []).length} `
        + `keyframe(s) (mode=${parsed.keyframe_mode})`,
      );
      return { actions: parsed.actions ?? [], token_usage: parsed.token_usage ?? [] };
    } catch (error: any) {
      const stderr: string = error.stderr ?? '';
      if (/"unsupported"\s*:\s*true/.test(stderr)) {
        const e = new Error('UNSUPPORTED_CAPTCHA: Cannot solve this animated captcha');
        (e as any).unsupported = true;
        throw e;
      }
      const apiError = parseApiError(stderr);
      if (apiError) throw apiError;
      console.error('Error executing CaptchaKraken solve-animated:', error);
      throw new Error(`Failed to execute the animated captcha solver: ${error.message}`);
    }
  }

  /**
   * Hold until the widget looks like `keyframePath` around the 0–1 point (cx, cy).
   *
   * This is the reason an animated answer names a frame. The model picked the
   * moment its target was visible, and the coordinates are only correct at that
   * moment; clicking as soon as the answer arrives lands on whatever the sprite
   * happens to be doing, which for a cross-fade is usually background.
   *
   * Only the neighbourhood of the action point is compared, with the same box and
   * metric the training label's frame was chosen with. Local rather than
   * whole-frame because everything ELSE in these puzzles is also moving: a
   * whole-frame match would need every unrelated sprite to align too, and would
   * essentially never open.
   *
   * Never throws. Returns whether the state was reached; on timeout the caller
   * clicks anyway (see `keyframeWaitTimeoutMs`).
   */
  private async waitForKeyframe(
    captchaElement: ElementHandle,
    keyframePath: string,
    cx: number,
    cy: number,
  ): Promise<boolean> {
    const timeout = this.config.keyframeWaitTimeoutMs ?? 6000;
    const interval = this.config.keyframeWaitPollMs ?? 120;
    const deadline = Date.now() + timeout;
    const probe = path.join(os.tmpdir(), `ck_kfwait_${Date.now()}_${Math.floor(Math.random() * 1e9)}.png`);
    let best = 1;
    try {
      while (Date.now() < deadline) {
        try {
          await captchaElement.screenshot({
            path: probe,
            timeout: this.config.elementScreenshotTimeoutMs ?? 8000,
            animations: 'disabled',
          });
          const r = await this.runCvTool(
            'match-region',
            { ref: keyframePath, live: probe, cx, cy },
            ['match-region', keyframePath, probe, String(cx), String(cy)],
          );
          if (typeof r?.diff === 'number') best = Math.min(best, r.diff);
          if (r?.match) {
            console.log(`[animated] widget matched the chosen keyframe (diff=${r.diff.toFixed(4)})`);
            return true;
          }
        } catch {
          // A failed probe is one lost poll, not a failed solve.
        }
        await delay(interval);
      }
    } finally {
      if (fs.existsSync(probe)) { try { fs.unlinkSync(probe); } catch { /* best-effort */ } }
    }
    console.log(
      `[animated] widget never matched the chosen keyframe within ${timeout}ms `
      + `(closest diff=${best.toFixed(4)}); clicking on the model's coordinates anyway`,
    );
    return false;
  }

  private async getSolution(imagePath: string, puzzleSource: 'hcaptcha' | 'recaptcha' | 'unknown' = 'unknown', retryMode: string | null = null, textMode = false): Promise<CliResponse> {
    // v2 ships a single provider: the JobHarvest vLLM server via the bundled
    // CaptchaKraken CLI. The CLI's planner reads VLLM_BASE_URL and the bearer
    // token (CAPTCHA_KRAKEN_API_KEY, falling back to VLLM_API_KEY) from the
    // environment; we also forward the key explicitly as a CLI arg below so it
    // works even when the subprocess doesn't inherit it.
    const {
      // vLLM LoRA name. Defaults to the full-puzzle `captcha` adapter
      // (JobHarvest/qwen3.5-9b-captcha-lora — solves grids AND click/drag/pixel
      // puzzles). Override in code or via CAPTCHA_LORA_NAME (e.g. `captcha-grid`
      // for the older grids-only adapter). Most users only set the endpoint URL
      // and, for the hosted API, CAPTCHA_KRAKEN_API_KEY.
      model = process.env.CAPTCHA_LORA_NAME ?? 'captcha',
      apiKey = process.env.CAPTCHA_KRAKEN_API_KEY ?? process.env.VLLM_API_KEY,
    } = this.config;

    // Dedup: if we've already asked the model about a byte-identical screenshot
    // under the same prompt (puzzle source + retry mode), the page hasn't
    // changed and another vLLM call would be wasted work. Reuse the answer.
    // The image bytes ARE the cache key — any real page change (tile refresh,
    // new challenge, fade) alters pixels and misses the cache, so this never
    // stales a genuinely-changed puzzle.
    let cacheKey: string | null = null;
    try {
      const imgHash = createHash('sha1').update(fs.readFileSync(imagePath)).digest('hex');
      cacheKey = `${imgHash}|${puzzleSource}|${retryMode ?? ''}|${textMode ? 'text' : ''}`;
      const cached = this.solutionCache.get(cacheKey);
      if (cached) {
        console.log('[dedup] identical screenshot already solved this session — skipping vLLM query.');
        // Reuse the actions but drop the token usage (no new tokens were spent).
        return { actions: cached.actions, token_usage: [] };
      }
    } catch {
      cacheKey = null; // hashing failed — fall through to a normal query
    }

    const { cliRoot, py } = this.resolveCli();

    const cmdParts = [
      py,
      '-m',
      'captchakraken.cli',
      `"${imagePath}"`,
      model,
      'captchaKrakenApi',
    ];

    if (apiKey) {
      cmdParts.push(apiKey);
    }

    // Always pass the vendor hint at the end as --puzzle-source=<vendor>; the
    // CLI's argparse falls through to the flag form for unknown trailing args.
    cmdParts.push(`--puzzle-source=${puzzleSource}`);
    if (retryMode) {
      cmdParts.push(`--retry-mode=${retryMode}`);
    }
    // The DOM said this puzzle has a text box, so the CLI must send the
    // distorted-text prompt and skip grid detection. The picture alone cannot
    // decide this — see the textMode note in solveSingle.
    if (textMode) {
      cmdParts.push('--text-mode');
    }

    const command = cmdParts.join(' ');
    console.log(`Executing CaptchaKraken CLI: ${command}`);

    try {
      const { stdout, stderr } = await execAsync(command, {
        cwd: cliRoot,
        // Only this call reaches the model, so it's the only one that needs the
        // session id — the other cliEnv() call sites run pure-OpenCV subcommands
        // that never touch the inference endpoint.
        env: cliEnv(cliRoot, this.solveSessionId ? { CAPTCHA_KRAKEN_SESSION: this.solveSessionId } : undefined),
        maxBuffer: 10 * 1024 * 1024 // Increase buffer for large outputs if needed
      });

      console.log('CaptchaKraken CLI stdout:', stdout);
      if (stderr) {
        console.error('CaptchaKraken CLI stderr:', stderr);
      }

      if (!stdout.trim()) {
        throw new Error(`CLI returned empty output. Stderr: ${stderr}`);
      }

      try {
        const lines = stdout.trim().split('\n');
        let actions: SolverResult = [];
        let tokenUsage: TokenUsage[] = [];

        for (const line of lines) {
          try {
            const parsed = JSON.parse(line);

            // Handle new format { actions: ..., token_usage: ... }
            if (parsed.actions !== undefined && parsed.token_usage !== undefined) {
              actions = parsed.actions;
              tokenUsage = parsed.token_usage;
              break;
            }

            // Fallback for old format or list of actions
            if (Array.isArray(parsed)) {
              actions = parsed;
            } else if (parsed.action && (parsed.target_bounding_box || parsed.target_coordinates || parsed.action === 'wait')) {
              actions = [parsed];
            }
          } catch (e) {
            // Not json or not relevant
          }
        }

        const response: CliResponse = { actions, token_usage: tokenUsage };
        // Cache under the screenshot hash so a byte-identical re-query this
        // session reuses this answer instead of hitting vLLM again.
        if (cacheKey) this.solutionCache.set(cacheKey, response);
        return response;
      } catch (parseError) {
        throw new Error(`Failed to parse CLI output: ${stdout}\nStderr: ${stderr}`);
      }

    } catch (error: any) {
      // The CLI emits {"unsupported": true} (exit 2) when the current frame is
      // neither a grid nor a checkbox — e.g. an hCaptcha click/drag puzzle.
      // Surface that as a distinct error the solve loop can recognize and fail
      // fast on (only the puzzle TYPE is unsupported; a not-yet-rendered widget
      // is handled separately by DOM-presence waiting before we ever get here).
      const stderr: string = error.stderr ?? '';
      if (/"unsupported"\s*:\s*true/.test(stderr)) {
        const e = new Error('UNSUPPORTED_CAPTCHA: Cannot solve this kind of captcha');
        (e as any).unsupported = true;
        throw e;
      }

      // The hosted API refused the solve and said why (out of credits, rate
      // limited, attempt abandoned…). The CLI exits 3 with the already-worded
      // sentence plus the machine-readable fields; both are rethrown as-is.
      //
      // Wrapping this in "Failed to execute captcha solver CLI: Command failed
      // …" — which is what happens to every other non-zero exit — would bury a
      // billing problem under a sentence about a subprocess. camoufox surfaces
      // whatever we throw, so this is the message its users actually read.
      const apiError = parseApiError(stderr);
      if (apiError) throw apiError;

      console.error('Error executing CaptchaKraken CLI:', error);
      if (error.stdout) console.log('CLI stdout on error:', error.stdout);
      if (error.stderr) console.error('CLI stderr on error:', error.stderr);
      throw new Error(`Failed to execute captcha solver CLI: ${error.message}`);
    }
  }

  /**
   * Query the model for `initialShot`, then guard against the captcha frame
   * having changed DURING inference. reCAPTCHA/hCaptcha fade fresh tiles in over
   * ~1s; if new imagery painted while the model was generating, its answer
   * targets a stale ("undeveloped") frame and its tile picks / bboxes no longer
   * line up with what's on screen. After the model returns we re-screenshot the
   * element and diff it against the frame we sent (reusing the `check-movement`
   * primitive); if it moved beyond the threshold we discard the answer and
   * re-solve on the fresh frame, up to `maxStaleFrameReSolves` times, then act on
   * the latest answer rather than spin.
   *
   * `runQuery` performs the actual model call for a given screenshot path (the
   * caller wraps it in idle-wander where appropriate); it is re-invoked with the
   * fresh path on each re-solve. Token usage from every query — including
   * discarded ones — is accumulated, since those tokens were really spent. The
   * caller owns `initialShot`; every fresh frame captured here is created and
   * cleaned up here. Best-effort: a screenshot/diff failure falls through to the
   * answer already in hand.
   */
  private async solveFrameFreshnessGuarded(
    captchaElement: ElementHandle,
    initialShot: string,
    runQuery: (imagePath: string) => Promise<CliResponse>,
  ): Promise<CliResponse> {
    const enabled = this.config.staleFrameReSolveEnabled !== false;
    const threshold = this.config.staleFrameDiffThreshold ?? 0.02;
    const maxReSolves = this.config.maxStaleFrameReSolves ?? 2;

    const ownedFrames: string[] = []; // fresh frames WE captured (never initialShot)
    const mergedUsage: TokenUsage[] = [];
    try {
      let currentPath = initialShot;
      let response = await runQuery(currentPath);
      mergedUsage.push(...response.token_usage);
      if (!enabled) return response;

      for (let i = 0; i < maxReSolves; i++) {
        if (!(await this.captchaFrameChangedSince(captchaElement, currentPath, threshold))) {
          break; // frame held still through inference — the answer is valid.
        }
        const fresh = path.join(
          os.tmpdir(),
          `freshsolve_${Date.now()}_${Math.floor(Math.random() * 1e9)}.png`,
        );
        try {
          await captchaElement.screenshot({ path: fresh });
        } catch {
          break; // can't grab a fresh frame — act on the answer we have.
        }
        ownedFrames.push(fresh);
        this.saveImageForDebug(fresh);
        console.log(
          `[freshness] captcha frame changed during inference `
          + `(re-solve ${i + 1}/${maxReSolves}); the prior answer was for a stale `
          + `frame — re-querying on the developed one.`,
        );
        this.gridDebug('freshness:stale-frame', { reSolve: i + 1, threshold });
        currentPath = fresh;
        response = await runQuery(currentPath);
        mergedUsage.push(...response.token_usage);
      }
      return { actions: response.actions, token_usage: mergedUsage };
    } finally {
      for (const f of ownedFrames) {
        if (fs.existsSync(f)) {
          try { fs.unlinkSync(f); } catch { /* best-effort cleanup */ }
        }
      }
    }
  }

  /**
   * Screenshot the captcha element to a throwaway temp frame and diff it against
   * `priorPath` (the frame we sent the model). Returns true when the frame
   * changed beyond `threshold` — i.e. tiles faded in / refreshed since. Reuses
   * the persistent CV worker's `check-movement` (falling back to a one-shot
   * subprocess), the same frame-diff the settle detectors use. Cleans up its own
   * temp frame. Best-effort: any screenshot/diff failure returns false so the
   * caller acts on the answer it already has rather than spinning.
   */
  private async captchaFrameChangedSince(
    captchaElement: ElementHandle,
    priorPath: string,
    threshold: number,
  ): Promise<boolean> {
    const probe = path.join(
      os.tmpdir(),
      `freshcheck_${Date.now()}_${Math.floor(Math.random() * 1e9)}.png`,
    );
    try {
      await captchaElement.screenshot({ path: probe, timeout: 2500, animations: 'disabled' });
      const res = await this.runCvTool(
        'check-movement',
        { a: priorPath, b: probe, threshold },
        ['check-movement', priorPath, probe, String(threshold)],
      );
      return !!(res && res.has_movement);
    } catch {
      return false;
    } finally {
      if (fs.existsSync(probe)) {
        try { fs.unlinkSync(probe); } catch { /* best-effort cleanup */ }
      }
    }
  }

  /** Record a challenge-state transition. No-op for behaviour; logs the change
   *  (via gridDebug, so only when CAPTCHA_DEBUG=1) for offline diagnosis. */
  private setState(next: CaptchaState, note?: string): void {
    if (this.state === next) return;
    this.gridDebug('state', { from: this.state, to: next, ...(note ? { note } : {}) });
    this.state = next;
  }

  /**
   * Screenshot the challenge element to a throwaway file and return its sha1
   * content hash (or null on failure). Used to tell whether the frame has
   * changed (e.g. the post-submit transition to the next round). Cleans up.
   */
  private async elementFrameHash(el: ElementHandle): Promise<string | null> {
    const f = path.join(os.tmpdir(), `fh_${Date.now()}_${Math.floor(Math.random() * 1e9)}.png`);
    try {
      await el.screenshot({ path: f, timeout: 2500, animations: 'disabled' });
      return createHash('sha1').update(fs.readFileSync(f)).digest('hex');
    } catch {
      return null;
    } finally {
      if (fs.existsSync(f)) { try { fs.unlinkSync(f); } catch { /* best-effort */ } }
    }
  }

  /**
   * Monitor the challenge element until its pixels stop changing (settled), or
   * until it's clear it never will (animated / video). Polls screenshots and
   * diffs consecutive frames with `check-movement` (the persistent CV worker).
   * Returns:
   *   'settled'  — `settleFrames` consecutive frame-pairs showed no movement;
   *                safe to screenshot for the model.
   *   'animated' — it kept moving right up to `animatedChallengeAfterMs` without
   *                ever settling → very likely a video/animated puzzle.
   *   'timeout'  — neither happened within `settleTimeoutMs` (proceed best-effort).
   *
   * Note this is a *pixel* settle; the caller pairs it with the DOM-level
   * `waitForHcaptchaChallengeImages` so a static loading frame (spinner on grey,
   * below the pixel threshold) isn't mistaken for painted tiles. Cleans up.
   */
  private async waitForElementSettled(
    el: ElementHandle,
    opts?: { pollMs?: number; settleFrames?: number; maxMs?: number; animatedAfterMs?: number; threshold?: number },
  ): Promise<'settled' | 'animated' | 'timeout'> {
    const pollMs = opts?.pollMs ?? this.config.settlePollMs ?? 220;
    const settleFrames = opts?.settleFrames ?? this.config.settleFrames ?? 2;
    const maxMs = opts?.maxMs ?? this.config.settleTimeoutMs ?? 9000;
    const animatedAfterMs = opts?.animatedAfterMs ?? this.config.animatedChallengeAfterMs ?? 4500;
    const threshold = opts?.threshold ?? this.config.settleDiffThreshold ?? 0.01;
    const start = Date.now();
    let prev: string | null = null;
    let stillStreak = 0;
    const frames: string[] = [];
    const tmp = () => path.join(os.tmpdir(), `settle_${Date.now()}_${Math.floor(Math.random() * 1e9)}.png`);
    try {
      while (Date.now() - start < maxMs) {
        const f = tmp();
        // Short timeout + disabled animations: a closing/animating challenge
        // element otherwise makes Playwright's default 30s stability wait hang
        // per screenshot (that's what made a multi-round solve take ~115s). Fail
        // fast and skip the frame instead.
        try { await el.screenshot({ path: f, timeout: 2500, animations: 'disabled' }); }
        catch { await delay(pollMs); continue; }
        frames.push(f);
        if (prev) {
          const res = await this.runCvTool(
            'check-movement', { a: prev, b: f, threshold }, ['check-movement', prev, f, String(threshold)],
          );
          const moved = !!(res && res.has_movement);
          stillStreak = moved ? 0 : stillStreak + 1;
          const stale = frames.shift();
          if (stale && fs.existsSync(stale)) { try { fs.unlinkSync(stale); } catch { /* best-effort */ } }
          if (stillStreak >= settleFrames) return 'settled';
          // Still moving this late in → it's not just loading; call it animated.
          if (moved && (Date.now() - start) >= animatedAfterMs) return 'animated';
        }
        prev = frames[frames.length - 1];
        await delay(pollMs);
      }
      return 'timeout';
    } finally {
      for (const f of frames) {
        if (fs.existsSync(f)) { try { fs.unlinkSync(f); } catch { /* best-effort */ } }
      }
    }
  }

  /**
   * After a submit we EXPECT the challenge frame to change (advance to the next
   * round, or close because it was accepted). Poll until the element's content
   * hash differs from `sinceHash` — the transition beginning — so we never
   * screenshot/solve the pre-transition frame again. Returns true once it
   * changed, false if it never changed within `postSubmitChangeTimeoutMs` (e.g.
   * it was already the final state). Best-effort.
   */
  private async waitForChangeSince(
    el: ElementHandle,
    sinceHash: string,
    opts?: { pollMs?: number; maxMs?: number },
  ): Promise<boolean> {
    const pollMs = opts?.pollMs ?? this.config.settlePollMs ?? 220;
    const maxMs = opts?.maxMs ?? this.config.postSubmitChangeTimeoutMs ?? 4000;
    const start = Date.now();
    while (Date.now() - start < maxMs) {
      const h = await this.elementFrameHash(el);
      if (h && h !== sinceHash) return true;
      await delay(pollMs);
    }
    return false;
  }

  /**
   * Run `fn` (typically the model query) while idly drifting the cursor over
   * the captcha, so the mouse behaves like a human weighing the options instead
   * of freezing during inference. Uses the same generate_trajectory paths as real
   * clicks; cancelled the instant `fn` resolves. Best-effort — any wander error
   * is swallowed and never fails the solve. Disable via config.idleMouseWander.
   */
  private async withIdleWander<T>(
    page: Page,
    element: ElementHandle,
    fn: () => Promise<T>,
  ): Promise<T> {
    if (this.config.idleMouseWander === false) return fn();
    let box: { x: number; y: number; width: number; height: number } | null = null;
    try { box = await element.boundingBox(); } catch { box = null; }
    if (!box || box.width < 20 || box.height < 20) return fn();
    const b = box;

    let stop = false;
    const pad = 0.18; // keep drift inside the tile area, off the extreme edges
    const wander = (async () => {
      // brief pause before the first drift — don't lurch the instant we ask
      await delay(120 + Math.random() * 180);
      while (!stop) {
        const tx = b.x + b.width * (pad + Math.random() * (1 - 2 * pad));
        const ty = b.y + b.height * (pad + Math.random() * (1 - 2 * pad));
        try {
          await this.performSmoothMove(page, tx, ty);
        } catch {
          break;
        }
        if (stop) break;
        await delay(180 + Math.random() * 360); // human dwell between glances
      }
    })();

    try {
      return await fn();
    } finally {
      stop = true;
      await wander.catch(() => {});
    }
  }

  // Simplified move function with smooth movement
  async move(
    page: Page,
    selectorOrElement: string | ElementHandle,
    options: { paddingPercentage?: number } = {}
  ): Promise<void> {
    let elem: ElementHandle | null = null;
    if (typeof selectorOrElement === 'string') {
      elem = await page.waitForSelector(selectorOrElement, { state: 'visible', timeout: 10000 });
    } else {
      elem = selectorOrElement;
    }

    if (!elem) {
      throw new Error(`Element not found: ${selectorOrElement}`);
    }

    await elem.scrollIntoViewIfNeeded();

    const box = await elem.boundingBox();
    if (!box) {
      throw new Error(`Element has no bounding box: ${selectorOrElement}`);
    }

    // Default padding 25% to stay well inside the element
    const padding = (options.paddingPercentage || 25) / 100;
    const padX = box.width * padding;
    const padY = box.height * padding;

    // Pick a random point within the padded area
    const targetX = box.x + padX + Math.random() * (box.width - 2 * padX);
    const targetY = box.y + padY + Math.random() * (box.height - 2 * padY);

    await this.performSmoothMove(page, targetX, targetY);
  }

  async moveAndClick(page: Page, element: ElementHandle) {
    await this.move(page, element);
    await page.mouse.down();
    await delay(Math.random() * 20 + 20);
    await page.mouse.up();
  }

  private async performSmoothMove(page: Page, x: number, y: number) {
    // 60Hz sampling; see src/trajectory.ts (first-party, replaced cursory-ts)
    const [points, timings] = generate_trajectory(
      [this.lastMousePosition.x, this.lastMousePosition.y],
      [x, y],
      60 // 60 points per second
    );

    const SPEED_MULTIPLIER = 1;

    const vectors: TimedVector[] = [];

    for (let i = 0; i < points.length; i++) {
      vectors.push({
        x: points[i][0],
        y: points[i][1],
        timestamp: timings[i] / SPEED_MULTIPLIER // timings are cumulative from start
      });
    }

    await this.tracePath(page, vectors);
  }

  private async tracePath(page: Page, vectors: TimedVector[]) {
    // Get viewport for clamping
    let viewport: { width: number, height: number } = { width: 1920, height: 1080 };
    try {
      const vp = page.viewportSize();
      if (vp) viewport = vp;
    } catch (e) { }

    const startTime = Date.now();

    for (let i = 0; i < vectors.length; i++) {
      const v = vectors[i];

      try {
        // Clamp coordinates to viewport
        const clampedX = Math.max(0, Math.min(v.x, viewport.width));
        const clampedY = Math.max(0, Math.min(v.y, viewport.height));

        // Move mouse
        await page.mouse.move(clampedX, clampedY);

        // Update last position
        this.lastMousePosition = { x: clampedX, y: clampedY };

        // Calculate delay to match target timestamp
        if (v.timestamp !== undefined) {
          const targetTime = startTime + v.timestamp;
          const now = Date.now();
          const delayMs = targetTime - now;

          if (delayMs > 0) {
            await delay(delayMs);
          }
        }
      } catch (error) {
        // Check if page closed or other fatal errors if needed, otherwise ignore
        const errorMessage = error instanceof Error ? error.message : String(error);
        if (errorMessage.includes('Target closed') || errorMessage.includes('Session closed')) {
          log('Warning: could not move mouse, page or session closed.');
          return;
        }
      }
    }
  }

  private async executeClick(
    page: Page,
    element: ElementHandle,
    action: ClickAction,
    elementBox: { x: number, y: number, width: number, height: number }
  ) {
    let relativeX: number;
    let relativeY: number;

    if (action.target_bounding_box) {
      // Pick random point in padding
      const [minX, minY, maxX, maxY] = action.target_bounding_box;

      const pixelMinX = minX * elementBox.width;
      const pixelMaxX = maxX * elementBox.width;
      const pixelMinY = minY * elementBox.height;
      const pixelMaxY = maxY * elementBox.height;

      // Apply padding (10%)
      const paddingX = (pixelMaxX - pixelMinX) * 0.1;
      const paddingY = (pixelMaxY - pixelMinY) * 0.1;

      const safeMinX = pixelMinX + paddingX;
      const safeMaxX = pixelMaxX - paddingX;
      const safeMinY = pixelMinY + paddingY;
      const safeMaxY = pixelMaxY - paddingY;

      // Random position
      relativeX = safeMinX + Math.random() * (safeMaxX - safeMinX);
      relativeY = safeMinY + Math.random() * (safeMaxY - safeMinY);
    } else if (action.target_coordinates) {
      // [x, y] percentages
      const [xPct, yPct] = action.target_coordinates;
      relativeX = xPct * elementBox.width;
      relativeY = yPct * elementBox.height;
    } else {
      console.warn('Click action received without coordinates or bounding box', action);
      return;
    }

    const absoluteX = elementBox.x + relativeX;
    const absoluteY = elementBox.y + relativeY;

    // Use the shared smooth move method
    await this.performSmoothMove(page, absoluteX, absoluteY);

    // Perform click
    await page.mouse.down();
    await page.waitForTimeout(Math.random() * 30 + 20); // Random hold duration
    await page.mouse.up();
  }

  private async executeDrag(
    page: Page,
    _element: ElementHandle,
    action: { source_bounding_box: [number, number, number, number]; target_bounding_box: [number, number, number, number] },
    elementBox: { x: number, y: number, width: number, height: number }
  ) {
    const bboxCenter = (bbox: [number, number, number, number]) => {
      const cx = elementBox.x + ((bbox[0] + bbox[2]) / 2) * elementBox.width;
      const cy = elementBox.y + ((bbox[1] + bbox[3]) / 2) * elementBox.height;
      return { x: cx, y: cy };
    };
    const src = bboxCenter(action.source_bounding_box);
    const dst = bboxCenter(action.target_bounding_box);

    await this.performSmoothMove(page, src.x, src.y);
    await page.mouse.down();
    await page.waitForTimeout(Math.random() * 50 + 50);
    await this.performSmoothMove(page, dst.x, dst.y);
    await page.waitForTimeout(Math.random() * 50 + 50);
    await page.mouse.up();
  }

  // ────────────────────────────────────────────────────────── typing + sliding
  // Mirrors _find_control / _execute_type / _execute_slide in page_solver.py.

  /**
   * First VISIBLE match for `selectors`, tried in order.
   *
   * `scope` is the challenge frame, or — for the vendors that render into the
   * host page rather than an iframe — the widget element itself. Never the
   * page: the generic tail of both selector tables would otherwise happily
   * match a login form's text box or a carousel's drag handle somewhere else on
   * the document, and the answer would go there.
   */
  private async findControl(
    scope: Frame | ElementHandle,
    selectors: ReadonlyArray<string>,
  ): Promise<ElementHandle | null> {
    for (const selector of selectors) {
      try {
        const el = await scope.$(selector);
        if (el && await el.isVisible()) return el;
      } catch {
        // A selector this adapter can't parse must not end the search.
      }
    }
    return null;
  }

  /** Put the model's reading of a distorted-text captcha into its box. */
  private async executeType(
    page: Page,
    scope: Frame | ElementHandle,
    action: TypeAction,
  ): Promise<boolean> {
    const text = action.text ?? '';
    if (!text) return false;
    const field = await this.findControl(scope, TEXT_INPUT_SELECTORS);
    if (!field) {
      console.warn('Type action, but no text box in the widget; skipping.');
      return false;
    }

    await this.moveAndClick(page, field);  // travel there, then press to focus
    // A retry round arrives with the previous attempt still in the box, and
    // typing would APPEND to it — submitting a string the model never read.
    try {
      await page.keyboard.press('Control+A');
    } catch { /* an adapter without a keyboard shortcut path */ }
    // Per character rather than one type(text, {delay}) call: a constant
    // inter-key delay is itself a signal, and these are the vendors that score
    // typing cadence.
    for (const ch of text) {
      try {
        await page.keyboard.type(ch);
      } catch (e) {
        console.warn('Could not type into the captcha field:', e);
        return false;
      }
      await page.waitForTimeout(Math.random() * 90 + 45);
    }
    console.log(`Typed ${text.length} character(s) into the captcha field.`);
    return true;
  }

  /** `captchakraken track-piece` — box of what moved, handle masked out. */
  private async trackPiece(
    element: ElementHandle,
    beforePath: string,
    afterPath: string,
    exclude: [number, number, number, number],
  ): Promise<[number, number, number, number] | null> {
    try {
      await element.screenshot({
        path: afterPath,
        timeout: this.config.elementScreenshotTimeoutMs ?? 8000,
        animations: 'disabled',
      });
      const res = await this.runCvTool(
        'track-piece',
        { before: beforePath, after: afterPath, exclude },
        ['track-piece', beforePath, afterPath, JSON.stringify(exclude)],
      );
      return res && res.bbox ? res.bbox : null;
    } catch (e) {
      console.warn('track-piece failed:', e);
      return null;
    }
  }

  /**
   * Drive a puzzle-piece slider until the PIECE reaches the model's slot.
   *
   * The model is asked for one thing here — the centre of the gap — because it
   * is the only thing the picture can tell it. What it cannot know is how far
   * the handle must travel to put the piece there: the handle is elsewhere on
   * the widget, and the ratio between the two is a vendor implementation detail
   * that several of them deliberately vary.
   *
   * So this is closed-loop, not a calculation. Press the handle, nudge it twice
   * by known amounts, and watch the screen: union(before, after) spans the
   * piece's ORIGINAL left edge to its CURRENT right edge, so its width is
   * pieceWidth + ratio x nudge. Two nudges, two widths, two unknowns — solve for
   * both, then steer the remaining distance and re-measure. The mouse is not
   * released until the piece is home, because on every one of these puzzles
   * releasing IS the submit; there is no Verify button to reconsider at.
   *
   * Returns false if there is nothing here to drag, leaving the caller's normal
   * no-op handling to deal with it.
   */
  private async executeSlide(
    page: Page,
    element: ElementHandle,
    scope: Frame | ElementHandle,
    action: DragAction,
    elementBox: { x: number, y: number, width: number, height: number },
  ): Promise<boolean> {
    const targetX = ((action.target_bounding_box[0] + action.target_bounding_box[2]) / 2)
      * elementBox.width;

    const handle = await this.findControl(scope, SLIDER_HANDLE_SELECTORS);
    if (!handle) {
      // No track — the sliderless members of the family (Lemin's "cropped")
      // want the piece dragged directly. Same answer from the model, because
      // the two look identical; different gesture. Nothing to close a loop on,
      // since the piece is under the cursor and moves with it one for one.
      const piece = await this.findControl(scope, DRAGGABLE_PIECE_SELECTORS);
      const box = piece ? await piece.boundingBox() : null;
      if (!box) {
        console.warn('Slide action, but the widget has neither a slider nor a draggable piece.');
        return false;
      }
      // BOTH axes. The rail members travel horizontally and nothing else, so
      // the handle's own y is the only y there is — but a free drag carries the
      // piece across the card, and holding the piece's row here slid it along
      // the TRAY and released it there, well below the slot, every time.
      const targetY = ((action.target_bounding_box[1] + action.target_bounding_box[3]) / 2)
        * elementBox.height;
      console.log('No slider track; dragging the piece to the slot directly.');
      await this.performSmoothMove(page, box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.waitForTimeout(Math.random() * 50 + 50);
      await this.performSmoothMove(page, elementBox.x + targetX, elementBox.y + targetY);
      await page.waitForTimeout(Math.random() * 50 + 50);
      await page.mouse.up();
      return true;
    }

    const hbox = await handle.boundingBox();
    if (!hbox) return false;
    const startX = hbox.x + hbox.width / 2;
    const holdY = hbox.y + hbox.height / 2;

    // Mask the whole horizontal BAND the handle runs in, not just where it is
    // now: it is about to move across that band, and most vendors fill the
    // track behind it as it goes. Either would otherwise be the largest moving
    // thing in frame, and we would track the handle instead of the piece.
    const pad = Math.max(4, hbox.height * 0.35);
    const exclude: [number, number, number, number] = [
      0,
      hbox.y - elementBox.y - pad,
      elementBox.width,
      hbox.y + hbox.height - elementBox.y + pad,
    ];

    const shots = Array.from({ length: 4 }, (_, i) =>
      path.join(os.tmpdir(), `slide_${Date.now()}_${i}_${Math.floor(Math.random() * 1e9)}.png`));
    try {
      await this.move(page, handle, { paddingPercentage: 30 });
      await page.mouse.down();
      await page.waitForTimeout(Math.random() * 60 + 60);
      await element.screenshot({
        path: shots[0],
        timeout: this.config.elementScreenshotTimeoutMs ?? 8000,
        animations: 'disabled',
      });

      const widths: Array<[number, number]> = [];
      let lastBox: [number, number, number, number] | null = null;
      for (let i = 0; i < SLIDE_PROBE_OFFSETS_PX.length; i++) {
        const offset = SLIDE_PROBE_OFFSETS_PX[i];
        await this.performSmoothMove(page, startX + offset, holdY);
        await page.waitForTimeout(Math.random() * 40 + 40);
        const box = await this.trackPiece(element, shots[0], shots[i + 1], exclude);
        if (box) {
          widths.push([offset, box[2] - box[0]]);
          lastBox = box;
        }
      }

      const { pieceWidth, ratio } = solveSlideGeometry(widths, elementBox.width);
      if (!lastBox || pieceWidth === null) {
        // Never saw the piece — a canvas the screenshot cannot separate, a
        // widget that redraws wholesale, or a press the handle refused. Fall
        // back on the geometry every one of these puzzles shares: piece and
        // handle both start flush left, so the handle's travel is the piece's.
        console.warn('Slider: piece never resolved on screen; steering by handle travel alone.');
        await this.performSmoothMove(page, startX + (targetX - (startX - elementBox.x)), holdY);
      } else {
        // The offset lastBox was MEASURED at — not the final probe, and not
        // indexed by how many measurements succeeded. If the first probe failed
        // to resolve and the second worked, those two disagree, and steering
        // from a base the reading does not belong to sends the piece somewhere
        // neither the model nor the screen asked for.
        let offset = widths[widths.length - 1][0];
        for (let i = 0; i < SLIDE_MAX_CORRECTIONS; i++) {
          const pieceCentre = lastBox[2] - pieceWidth / 2;
          const error = targetX - pieceCentre;
          if (Math.abs(error) <= SLIDE_TOLERANCE_PX) break;
          offset += error / ratio;
          await this.performSmoothMove(page, startX + offset, holdY);
          await page.waitForTimeout(Math.random() * 40 + 40);
          const box = await this.trackPiece(element, shots[0], shots[3], exclude);
          if (!box) break;  // ran out of track; release where we are
          lastBox = box;
        }
      }

      // Settle before letting go. A release in the same tick as the last move
      // reads as a machine, and some vendors sample the final milliseconds of
      // the gesture.
      await page.waitForTimeout(Math.random() * 120 + 90);
    } finally {
      try { await page.mouse.up(); } catch { /* the page may have navigated */ }
      for (const shot of shots) {
        try { if (fs.existsSync(shot)) fs.unlinkSync(shot); } catch { /* best-effort */ }
      }
    }
    return true;
  }
}
