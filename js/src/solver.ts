/**
 * Browser page driver. Finds the widget, photographs it, asks the bundled Python engine what to do,
 * and performs the answer. Mirrors python/src/captchakraken/page_solver.py.
 */
import {
  PlaywrightPage as Page,
  PlaywrightElementHandle as ElementHandle,
  PlaywrightFrame as Frame,
  PlaywrightLocator as Locator,
  PlaywrightScope as Scope,
} from './playwright-types';
import { watchPage, CaptchaWatcher, WatchOptions } from './watcher';
import { Humanizer, resolveHumanizer } from './humanize.js';
import { execFile, spawn, spawnSync, ChildProcessWithoutNullStreams } from 'child_process';
import { promisify } from 'util';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import { createHash, randomUUID } from 'crypto';
import { PhaseBudget, timingsEnabled } from './timing';
import { CaptchaKrakenConfig, SolverResult, ClickAction, DragAction, TypeAction, CaptchaAction, SolveResult, CliResponse, TokenUsage, Vector } from './types';
import { ActionKind, FrameRole, KeyframeMode, Outcome, PaintVerdict, PauseKind, Phase, RecaptchaBanner, RetryMode, SettleVerdict, SolveStage, Vendor, isOneOf } from './kinds';
import { aggregateTokenUsage } from './token-usage';
import { parseApiError } from './errors';
import { DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS } from './limits';
import { resolvePythonCommand } from './python-command';
import { buildSolveArgs, redactCommand, solveEnv } from './cli-invocation';
import { solveSlideGeometry, MIN_PIECE_PX, MAX_PIECE_FRACTION } from './slide-geometry';
import { getBundledCliRoot, resolveLoraName } from './model-name';
import { SELECTORS, VENDORS, VendorSelectors, WIDGET_PROBES, WidgetProbe, RESPONSE_SELECTORS, ACCEPTED_SELECTORS, SUBMIT_SELECTORS,
  TEXT_INPUT_SELECTORS, TEXT_INPUT_VENDOR_SELECTORS, SLIDER_HANDLE_SELECTORS, PIECE_SELECTORS } from './selectors';

const execFileAsync = promisify(execFile);
const log = (message: string, ...args: any[]) => console.log(`[Solver] ${message}`, ...args);
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
const tmp = (prefix: string) => path.join(os.tmpdir(), `${prefix}_${Date.now()}_${Math.floor(Math.random() * 1e9)}.png`);
const unlink = (f: string | null | undefined) => { if (f && fs.existsSync(f)) { try { fs.unlinkSync(f); } catch { /* best-effort */ } } };
const rmdir = (d: string | null | undefined) => { if (d) { try { fs.rmSync(d, { recursive: true, force: true }); } catch { /* best-effort */ } } };
const sha1 = (f: string) => createHash('sha1').update(fs.readFileSync(f)).digest('hex');
const bboxCenter = (b: [number, number, number, number]): [number, number] => [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];

type Box = { x: number; y: number; width: number; height: number };

function commandExists(command: string): boolean {
  try {
    const probe = spawnSync(command, ['--version'], { stdio: 'ignore', shell: false });
    return !probe.error && probe.status === 0;
  } catch {
    return false;
  }
}

function getVenvPython(cliRoot: string): string | null {
  for (const rel of ['bin/python', 'bin/python3', 'Scripts/python.exe', 'Scripts/python']) {
    const c = path.join(cliRoot, '.venv', rel);
    if (fs.existsSync(c)) return c;
  }
  return null;
}

/** Env for the Python CLI: the bundled `python/src` goes first on PYTHONPATH so the engine imports without a pip install. */
function cliEnv(cliRoot: string, extra?: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const srcDir = path.join(cliRoot, 'src');
  return { ...process.env, PYTHONPATH: process.env.PYTHONPATH ? `${srcDir}${path.delimiter}${process.env.PYTHONPATH}` : srcDir, ...extra };
}

/** Width and height from the IHDR chunk, so no image-size dependency is needed. */
function readPngDimensions(filePath: string): { width: number; height: number } | null {
  try {
    const fd = fs.openSync(filePath, 'r');
    try {
      const buf = new Uint8Array(24);
      if (fs.readSync(fd, buf, 0, 24, 0) < 24 || buf[1] !== 0x50 || buf[2] !== 0x4e || buf[3] !== 0x47) return null;
      const beU32 = (o: number) => (buf[o] << 24 | buf[o + 1] << 16 | buf[o + 2] << 8 | buf[o + 3]) >>> 0;
      const width = beU32(16);
      const height = beU32(20);
      return width && height ? { width, height } : null;
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return null;
  }
}

interface GridSession {
  gridBoxes: number[][];
  elementBox: Box;
  scaleX: number;
  scaleY: number;
  screenshotW: number;
  screenshotH: number;
}

interface GridCellStates {
  empty: number[];
  changing: number[];
  loaded: number[];
  selected: number[];
}

interface TrackedPiece {
  bbox: [number, number, number, number];
  piece: { centre: number, width: number } | null;
}

/** What detection found: the handle to photograph, the locator to search inside, and which table entry named it. */
export interface Widget {
  el: ElementHandle;
  at: Locator;
  vendor: Vendor;
  role: FrameRole;
}

// A named set, not `=== UNKNOWN`: naming a new vendor would otherwise silently switch off typed-challenge
// detection for MTCaptcha/Yandex/BotDetect and the animated probe for GeeTest/Tencent.
const VENDORS_WITH_BESPOKE_HANDLING: ReadonlySet<Vendor> = new Set<Vendor>([Vendor.HCAPTCHA, Vendor.RECAPTCHA]);

// Long enough to resolve a locator `all()` just returned, short enough that a widget gone in between reads as stale, not hung.
const HANDLE_TIMEOUT_MS = 1000;

/** Every visible match of `selectors`, in selector order, queried at once. A selector this adapter can't parse is skipped, not fatal. */
const visible = async (scope: Scope, selectors: readonly string[]): Promise<Locator[]> =>
  (await Promise.all(selectors.map((s) => scope.locator(s).filter({ visible: true }).all().catch(() => [])))).flat();
const handleOf = (at: Locator): Promise<ElementHandle | null> => at.elementHandle({ timeout: HANDLE_TIMEOUT_MS }).catch(() => null);
const handles = async (ats: Locator[]): Promise<ElementHandle[]> => (await Promise.all(ats.map(handleOf))).filter((h): h is ElementHandle => h !== null);
const frameOf = async (at: Locator): Promise<Frame | null> => (await (await handleOf(at))?.contentFrame().catch(() => null)) ?? null;
const hasText = async (el: ElementHandle): Promise<boolean> => !!((await el.textContent().catch(() => null)) ?? '').trim();

const SLIDE_TOLERANCE_PX = 2;
const SLIDE_MAX_CORRECTIONS = 3;

/** An allow-list of what needs nothing: the other direction raises instead of clicking at the origin. */
export function answerNeedsElementBox(actions: ReadonlyArray<{ action?: string }>): boolean {
  return actions.some((a) => a?.action !== ActionKind.DONE);
}

/** The widget moved on under us (hCaptcha swapped rounds, GeeTest closed on accept): re-detect, do not fail. */
export function isStaleHandleError(message: string): boolean {
  return /Timeout .*exceeded|not visible|not attached|detached|Target closed|bounding box of captcha element/i.test(message);
}

// Frame-diff thresholds; two screens of one board differ by ~0.0056, a different board by ~0.77.
const NOT_THIS_BOARD_DIFF = 0.5;
// Tighter than staleFrameDiffThreshold (0.02), which was blind to GeeTest svg; 0.001 is the noise floor.
const MOVED_DURING_INFERENCE_DIFF = 0.002;
// hCaptcha odd-animal showed 38 screens in 4s with no repeat; past DEFAULT_MAX_KEYFRAMES a keyframe answer cannot describe the motion.
const BURST_ANIMATED_SCREENS = 6;
const NOT_THIS_BOARD_POLLS = 3;

export const SOLVE_DEFAULTS = {
  maxSolveLoops: 6,
  overallSolveTimeoutMs: 45_000,
  // 9s holds a 3-screen GeeTest svg cycle at 2.7s a screen; 6s gave up one screen short. videoBudgetMs derives from it on both ports.
  keyframeWaitTimeoutMs: 9_000,
} as const;

/** A hang detector for a burst whose screenshot never returns, sized off the ceiling. */
export function burstHangDeadlineMs(cfg: { videoBurstMaxMs?: number }): number {
  return 3 * (cfg.videoBurstMaxMs ?? 12_000) + 5_000;
}

export class CaptchaKrakenSolver {
  private config: CaptchaKrakenConfig;
  private videoBudgetMs = 0;
  private videoBudgetGranted = false;
  private human: Humanizer;
  private stepIndex = 0;
  private solveStartMs = 0;
  private cvWorker: ChildProcessWithoutNullStreams | null = null;
  private cvWorkerReady: Promise<boolean> | null = null;
  private cvWorkerSeq = 0;
  private cvWorkerPending: Map<number, { resolve: (v: any) => void; reject: (e: any) => void }> = new Map();
  private cvWorkerBuf = '';
  /** Answers keyed by screenshot hash; a hit means the answer already ran and changed nothing. */
  private solutionCache: Map<string, CliResponse> = new Map();
  private repeatedAnswerSeen = false;
  private knownAnimated = false;
  private animatedProbeDone = false;
  /** The one recording and one answer for the animated board on screen. */
  private animatedPlan: { burstDir: string; response: CliResponse } | null = null;
  private keyframeMode: KeyframeMode | null = null;
  private keyframeSteadyScreens = 0;
  private solveDeadlineAt = 0;
  private lastAnswerSig: string | null = null;
  private noProgressRounds = 0;
  private resampleLevel = 0;
  /** The recording `classifyByRecording` started and left running for the branch below to finish or drop. */
  private pendingBurst: ReturnType<CaptchaKrakenSolver['startKeyframeBurst']> | null = null;
  /**
   * The camera on the animated board in front of us, running across rounds until that board is gone.
   *
   * Distinct from `pendingBurst`, which is the speculative film one round starts and the same round spends.
   * This one outlives a rejected answer on purpose: it is the only thing that can give the next ask a
   * different question to answer, since the answer itself is deterministic at temperature 0.
   */
  private animatedFilm: ReturnType<CaptchaKrakenSolver['startKeyframeBurst']> | null = null;
  private lastBurstFps: number | null = null;
  private actedOnBoard = false;
  private lastSubmitFrameHash: string | null = null;
  private solveSessionId: string | null = null;
  private cliCache: { cliRoot: string; py: string } | null = null;
  private loraNameCache: string | null = null;
  budget: PhaseBudget | null = null;

  private ph<T>(name: Phase, fn: () => Promise<T>): Promise<T> {
    return this.budget ? this.budget.phase(name, fn) : fn();
  }

  constructor(config: CaptchaKrakenConfig = {}) {
    this.config = config;
    this.human = resolveHumanizer({ ...config, startingMousePosition: config.startingMousePosition ?? { x: 100, y: 100 } });
  }

  private get lastMousePosition(): Vector {
    return { x: this.human.at[0], y: this.human.at[1] };
  }

  private set lastMousePosition(v: Vector) {
    this.human.at = [v.x, v.y];
  }

  /** `disabled` freezes CSS animation, which once sliced GeeTest svg to a static clip; callers filming motion pass `allow`. */
  private shot(el: ElementHandle, p: string, timeout = 2500, animations: 'disabled' | 'allow' = 'disabled'): Promise<Buffer> {
    return el.screenshot({ path: p, timeout, animations });
  }

  async solve(page: Page): Promise<SolveResult | void> {
    // One session id per solve groups its inference rounds into one billable attempt.
    this.solveSessionId = randomUUID();
    this.budget = new PhaseBudget();
    let solvedForReport = false;
    try {
      const result = await this.solveImpl(page);
      if (result) result.phases = this.budget.toObject();
      solvedForReport = result?.isSolved === true;
      return result;
    } finally {
      if (timingsEnabled()) console.error(this.budget!.report());
      // The camera outlives a round by design, so it has to be ended by the thing that outlives the solve.
      await this.stopAnimatedFilm();
      this.teardownCvWorker();
      this.cvWorkerReady = null;
      this.reportOutcome(this.solveSessionId, solvedForReport);
      this.solveSessionId = null;
    }
  }

  /** Tell the hosted API whether the widget accepted, through the CLI, detached, never awaited. */
  private reportOutcome(sessionId: string | null, solved: boolean): void {
    // The opt-out is honoured here too: otherwise every solve spawns a process just to be told 404 by a local vLLM.
    if (!sessionId || process.env.CAPTCHA_REPORT_OUTCOME === '0') return;
    try {
      const { cliRoot, py } = this.resolveCli();
      const child = spawn(py, ['-m', 'captchakraken.cli', 'report-outcome', sessionId, solved ? Outcome.SOLVED : Outcome.FAILED],
        { cwd: cliRoot, env: cliEnv(cliRoot), detached: true, stdio: 'ignore' });
      child.on('error', () => {});
      child.unref();
    } catch { /* a missing engine is loud everywhere else */ }
  }

  /** Solve captchas on `page` as they appear, until `stop()`. Injects nothing into the page. */
  watch(page: Page, options: WatchOptions = {}): CaptchaWatcher {
    return watchPage(this, page, options);
  }

  private async solveImpl(page: Page): Promise<SolveResult | void> {
    const cfg = this.config;
    const maxSolveLoops = cfg.maxSolveLoops ?? SOLVE_DEFAULTS.maxSolveLoops;
    const overallSolveTimeoutMs = cfg.overallSolveTimeoutMs ?? SOLVE_DEFAULTS.overallSolveTimeoutMs;
    const start = Date.now();
    const cumulativeTokenUsage: TokenUsage[] = [];
    this.stepIndex = 0;
    this.solveStartMs = start;
    await this.human.reset(page);
    await this.stopAnimatedFilm();
    this.resetSolveState();
    const done = (): SolveResult => ({ isSolved: true, finalMousePosition: this.lastMousePosition, tokenUsage: aggregateTokenUsage(cumulativeTokenUsage) });

    let pendingRetryMode: RetryMode | null = null;
    let alreadyRetriedRecaptchaError = false;
    let unsupportedRetries = 0;
    let staleElementRetries = 0;
    let hasInteracted = false;
    let renderWaits = 0;
    // Strictly fewer than the loops, else the reCAPTCHA v3 "no interactive widget" branch never gets a turn.
    const MAX_RENDER_WAITS = Math.min(6, maxSolveLoops - 1);

    for (let attempt = 1; attempt <= maxSolveLoops; attempt++) {
      const budgetMs = overallSolveTimeoutMs + this.videoBudgetMs;
      this.solveDeadlineAt = start + budgetMs;
      if (Date.now() - start > budgetMs) {
        throw new Error(`Captcha solve timed out after ${budgetMs}ms (attempt ${attempt}/${maxSolveLoops})`
          + (this.videoBudgetMs ? `, including ${this.videoBudgetMs}ms granted for recording an animated challenge` : '') + '.');
      }
      if (hasInteracted && await this.isCaptchaSolved(page)) {
        console.log('Vendor reports solved; returning without another detect pass.');
        return done();
      }

      const widget = await this.ph(Phase.DETECT, () => this.detectCaptcha(page));
      if (!widget) {
        if (hasInteracted) {
          console.log('No supported captcha found (post-interaction); considering solved.');
          return done();
        }
        if (await this.hasInteractiveWidgetInDom(page) && renderWaits < MAX_RENDER_WAITS) {
          renderWaits++;
          console.log(`Captcha widget present in DOM but not yet rendered; waiting (${renderWaits}/${MAX_RENDER_WAITS}).`);
          await delay(800 + Math.random() * 300);
          continue;
        }
        throw new Error(await this.noWidgetMessage(page));
      }

      console.log(`\n--- Captcha Solve Loop ${attempt}/${maxSolveLoops} ---`);
      const retryModeThisLoop = pendingRetryMode;
      pendingRetryMode = null;

      let didInteract: boolean;
      let tokenUsage: TokenUsage[];
      try {
        ({ didInteract, tokenUsage } = await this.solveSingle(page, widget, attempt, retryModeThisLoop));
      } catch (e: any) {
        if (e?.animated) throw new Error(`Animated challenge could not be solved: ${e.message ?? 'recording failed'}`);
        if (e?.unsupported) {
          // Mid-solve, a transitional blank frame reads as unsupported; settle and retry.
          if (hasInteracted && unsupportedRetries < (cfg.maxUnsupportedReSolves ?? 3)) {
            unsupportedRetries++;
            const again = await this.detectCaptcha(page);
            if (again && await this.ph(Phase.SETTLE, () => this.waitForElementSettled(again.el)) === SettleVerdict.ANIMATED && cfg.videoSolveEnabled === false) {
              throw new Error('Animated/video challenge detected — the puzzle never settles and videoSolveEnabled is off.');
            }
            console.log(`"unsupported" mid-solve; settled and retrying (${unsupportedRetries}/${cfg.maxUnsupportedReSolves ?? 3}).`);
            continue;
          }
          throw new Error(`Cannot solve this kind of captcha — ${e?.message ?? e}`);
        }
        const emsg = String((e && (e as any).message) || e);
        if (hasInteracted && staleElementRetries < (cfg.maxStaleElementRetries ?? 3) && isStaleHandleError(emsg)) {
          staleElementRetries++;
          console.log(`stale challenge handle after submit ("${emsg.split('\n')[0]}"); re-detecting next round (${staleElementRetries}/${cfg.maxStaleElementRetries ?? 3}).`);
          await delay(cfg.staleElementBackoffMs ?? 900);
          continue;
        }
        throw e;
      }
      hasInteracted = hasInteracted || didInteract;

      if (this.noProgressRounds >= (cfg.maxNoProgressRounds ?? 2)) {
        throw new Error(`No progress: the model returned the same answer ${this.noProgressRounds + 1} times running and the challenge is still up (attempt ${attempt}/${maxSolveLoops}). Total usage: ${JSON.stringify(aggregateTokenUsage(cumulativeTokenUsage))}`);
      }
      renderWaits = 0;
      cumulativeTokenUsage.push(...tokenUsage);

      // One polled wait per round, not a flat sleep: the sleep observed nothing and cost 1200-1500ms a round, and
      // hCaptcha keeps its iframe visible ~2s while verifying, which read as a fresh puzzle and burned ~18s.
      const settleMs = didInteract ? (cfg.postSolveOutcomeTimeoutMs ?? 1000) : (cfg.postSolveDelayMs ?? 1200) + Math.random() * 300;
      const deadline = Date.now() + settleMs;
      const verdictT0 = Date.now();
      let solved = false;
      let widgetGone = 0;
      while (Date.now() < deadline) {
        if (await this.isCaptchaSolved(page)) { solved = true; break; }
        widgetGone = (await this.detectCaptcha(page)) ? 0 : widgetGone + 1;
        if (widgetGone >= 2) { solved = true; break; }
        if (await this.isChallengeFreshlyRendered(page)) {
          this.resampleLevel = 0;
          this.actedOnBoard = false;
          break;
        }
        await delay(cfg.postSolveOutcomePollMs ?? 75);
      }
      this.budget?.add(didInteract ? Phase.AWAIT_VERDICT : Phase.POST_SUBMIT_DELAY, Date.now() - verdictT0);
      if (solved) {
        console.log(`[verdict] success signal arrived after ${Date.now() - verdictT0}ms`);
        return done();
      }

      if (this.bannerIsFatalAfterRetry(await this.bannerKind(page))) {
        if (alreadyRetriedRecaptchaError) {
          throw new Error(`reCAPTCHA still showing the under-selection error after retry; aborting (model unable to identify the missed tile). Total usage: ${JSON.stringify(aggregateTokenUsage(cumulativeTokenUsage))}`);
        }
        console.log('reCAPTCHA returned under-selection error; retrying with missed-tiles prompt.');
        pendingRetryMode = RetryMode.MISSED_TILES;
        alreadyRetriedRecaptchaError = true;
      }

      if (!(await this.detectCaptcha(page))) return done();
      if (!didInteract && !this.noProgressRounds) {
        throw new Error(`Captcha still detected but solver performed no interactions; aborting to avoid an infinite loop. Total usage: ${JSON.stringify(aggregateTokenUsage(cumulativeTokenUsage))}`);
      }
    }

    throw new Error(`Captcha still detected after ${maxSolveLoops} solve loops. Total usage: ${JSON.stringify(aggregateTokenUsage(cumulativeTokenUsage))}`);
  }

  /** Fire the optional onStep observer with a fresh screenshot; best-effort, never fails the solve. */
  private async emitStep(captchaElement: ElementHandle, stage: SolveStage, label: string,
                         puzzleSource: Vendor, frameRole: FrameRole,
                         attempt: number, meta?: Record<string, any>): Promise<void> {
    const cb = this.config.onStep;
    if (!cb) return;
    this.stepIndex++;
    let screenshotPath: string | null = tmp(`step_${this.stepIndex}`);
    try {
      await this.shot(captchaElement, screenshotPath, this.config.stepScreenshotTimeoutMs ?? 2000);
    } catch {
      screenshotPath = null;
    }
    try {
      await cb({ index: this.stepIndex, stage, label, screenshotPath, puzzleSource, frameRole, attempt,
        elapsedMs: this.solveStartMs ? Date.now() - this.solveStartMs : 0, meta });
    } catch (e: any) {
      log(`onStep callback threw (ignored): ${e?.message ?? e}`);
    }
  }

  private async solveSingle(page: Page, widget: Widget, attempt: number, retryMode: RetryMode | null = null): Promise<{ didInteract: boolean, tokenUsage: TokenUsage[] }> {
    const cfg = this.config;
    const { el: captchaElement, vendor: puzzleSource, role: frameRole } = widget;
    const frame = await captchaElement.contentFrame();
    const scope: Scope = frame ?? widget.at;

    // Only the DOM can tell a typed captcha from a click puzzle; hCaptcha and reCAPTCHA never type.
    const textMode = !VENDORS_WITH_BESPOKE_HANDLING.has(puzzleSource) && (await this.answerBox(scope, widget.at)) !== null;
    if (textMode) console.log('Widget has a text box; solving as a distorted-text captcha.');

    if (frame && frameRole === FrameRole.CHALLENGE && SELECTORS[puzzleSource].images) {
      if (this.lastSubmitFrameHash) {
        await this.ph(Phase.AWAIT_NEXT_ROUND, () => this.waitForChangeSince(captchaElement, this.lastSubmitFrameHash as string));
        this.lastSubmitFrameHash = null;
      }
      await this.ph(Phase.HCAPTCHA_IMAGES, () => this.waitForBoardImages(frame, SELECTORS[puzzleSource]));
    }

    // A checkbox is clicked, not filmed, and a reCAPTCHA board is read by its grid below.
    const filmable = frameRole !== FrameRole.CHECKBOX && puzzleSource !== Vendor.RECAPTCHA && !textMode;
    let isAnimated = false;
    if (filmable && cfg.videoSolveEnabled === false) {
      if (await this.ph(Phase.SETTLE, () => this.waitForElementSettled(captchaElement)) === SettleVerdict.ANIMATED) {
        const e: any = new Error('ANIMATED_CHALLENGE: the challenge never settles and videoSolveEnabled is off.');
        e.animated = true;
        throw e;
      }
    } else if (filmable) {
      isAnimated = await this.classifyByRecording(captchaElement) === SettleVerdict.ANIMATED;
    }
    // hCaptcha keeps its challenge iframe visible ~2s after the final submit; read as a fresh puzzle it burned ~18s.
    if (frameRole === FrameRole.CHALLENGE && await this.isCaptchaSolved(page)) {
      console.log('[captchakraken] solved while waiting for the next round; skipping inference.');
      await this.releasePendingBurst();
      return { didInteract: false, tokenUsage: [] };
    }

    // 'settled' is not proof of static: a repeated answer buys ONE recording to find out, and the clip that
    // recording brings back is what decides — `knownAnimated` is set from it below, not from here.
    let probing = false;
    if (!isAnimated && this.knownAnimated) isAnimated = true;
    else if (!isAnimated && this.shouldRetryAsAnimated(puzzleSource)) {
      console.log('[animated] a second look at a board that did not solve as a still — recording it');
      this.animatedProbeDone = true;
      isAnimated = probing = true;
    }

    let establishedGridSize: number | null = null;
    // The challenge frame only: on the anchor this wasted an 8s grid-load timeout plus a find-grid subprocess.
    if (puzzleSource === Vendor.RECAPTCHA && frameRole === FrameRole.CHALLENGE) {
      await this.ph(Phase.GRID_LOAD, () => this.waitForGridCellsLoaded(captchaElement));
      const grid = await this.getGridBoxes(captchaElement);
      if (grid && grid.size === 3) {
        const elementBox = await captchaElement.boundingBox();
        if (elementBox) {
          await this.releasePendingBurst();
          return this.solveRecaptchaGrid(page, captchaElement, attempt, retryMode, grid, elementBox);
        }
      }
      establishedGridSize = grid?.size ?? null;
    }

    const painted = await this.ph(Phase.BOARD_PAINT, () => this.waitForBoardPainted(captchaElement));

    // The classifier's last frame is a settled still, unless the board painted while we watched.
    const screenshotPath = tmp('captcha');
    const settledFrame = (isAnimated || painted.waitedMs >= (cfg.boardPaintPollMs ?? 180)) ? null : (this.pendingBurst?.stableFrame() ?? null);
    if (settledFrame && fs.existsSync(settledFrame)) {
      fs.copyFileSync(settledFrame, screenshotPath);
    } else {
      await this.ph(Phase.SCREENSHOT, () => this.shot(captchaElement, screenshotPath, cfg.elementScreenshotTimeoutMs ?? 8000));
    }
    if (this.stepIndex === 0) {
      await this.emitStep(captchaElement, SolveStage.INITIAL, 'initial (pre-action)', puzzleSource, frameRole, attempt,
        establishedGridSize ? { gridSize: establishedGridSize } : undefined);
    }

    let performedAction = false;
    let slid = false;
    let answered = false;
    let allTokenUsage: TokenUsage[] = [];
    let burstDir: string | null = null;

    try {
      let response: CliResponse | null = null;
      const ask = (imagePath: string) => this.getSolution(imagePath, puzzleSource, retryMode, textMode);
      const askAnimated = () => this.ph(Phase.INFERENCE, () => this.withIdleWander(page, captchaElement, () => this.getAnimatedSolution(burstDir as string)));
      if (isAnimated) {
        // The camera was paused while the last answer was performed; from here the board is its own again.
        this.animatedFilm?.resume();
        if (this.animatedPlan) {
          burstDir = this.animatedPlan.burstDir;
          response = this.animatedPlan.response;
          console.log('[animated] reusing the recorded answer — same board, same screens');
        } else {
          const rec = this.animatedFilm ?? this.pendingBurst ?? this.startKeyframeBurst(captchaElement, true);
          this.pendingBurst = null;
          // First slice of a board waits for it to show a cycle or settle; a later one does not, because the
          // camera has been running through the whole previous round and already holds more than that.
          const first = this.animatedFilm !== rec;
          if (first) await this.ph(Phase.BURST, () => rec.settledOrCycled());
          const film = await rec.snapshot();
          if (probing) this.knownAnimated = film.moved;
          if (!probing || film.moved) {
            this.animatedFilm = rec;
            burstDir = film.dir;
            response = await askAnimated();
            this.animatedPlan = { burstDir, response };
          } else {
            // The clip answers the question the probe asked: a board that never moved is a still, and the
            // video expert can only answer a still with a frame number the widget will not take.
            console.log('[animated] the recording shows a still board; solving it as a still');
            const rested = rec.stableFrame();
            if (rested && fs.existsSync(rested)) fs.copyFileSync(rested, screenshotPath);
            rmdir(film.dir);
            if (this.animatedFilm === rec) this.animatedFilm = null;
            await rec.abandon();
            isAnimated = false;
          }
        }
      }
      if (!response && this.shouldSpeculate(puzzleSource, textMode)) {
        // Ask the still and film at once. No idle wander: a moving cursor reads as new screens.
        const rec = this.pendingBurst ?? this.startKeyframeBurst(captchaElement);
        this.pendingBurst = null;
        let still: CliResponse | null = null;
        let stillError: unknown = null;
        try {
          still = await this.ph(Phase.INFERENCE, () => this.solveFrameFreshnessGuarded(captchaElement, screenshotPath, ask, { recordingInFlight: true }));
        } catch (e) {
          stillError = e;
        }
        const cycling = await this.ph(Phase.BURST, () => rec.verdict());
        if (!cycling) {
          const movedOnce = rec.screensSeen() > 1;
          await rec.abandon();
          if (stillError) throw stillError;
          if (movedOnce) {
            // Moved once and settled: re-read the screen it came to rest on.
            console.log('[animated] the board changed once and settled — not a cycle; re-reading the screen it came to rest on.');
            try {
              await this.shot(captchaElement, screenshotPath, cfg.elementScreenshotTimeoutMs ?? 8000);
              still = await this.ph(Phase.INFERENCE, () => this.solveFrameFreshnessGuarded(captchaElement, screenshotPath, ask));
            } catch { /* keep the answer in hand */ }
          }
          response = still as CliResponse;
        } else {
          console.log('[animated] the widget moved while the model was reading it — dropping the still answer and finishing the recording.');
          isAnimated = this.knownAnimated = true;
          burstDir = (await this.ph(Phase.BURST, () => rec.finish())).dir;
          response = await askAnimated();
          this.animatedPlan = { burstDir, response };
        }
      } else if (!response) {
        response = await this.ph(Phase.INFERENCE, () => this.solveFrameFreshnessGuarded(captchaElement, screenshotPath,
          (imagePath) => this.withIdleWander(page, captchaElement, () => ask(imagePath))));
      }
      const actionList = Array.isArray(response.actions) ? response.actions : [response.actions];
      allTokenUsage = response.token_usage;

      // A `done` answer carries no coordinates, and several vendors close the widget the moment they accept.
      const elementBox = await captchaElement.boundingBox();
      if (!elementBox && answerNeedsElementBox(actionList)) throw new Error('Could not get bounding box of captcha element');
      const requireBox = () => {
        if (!elementBox) throw new Error('Could not get bounding box of captcha element');
        return elementBox;
      };

      // Tier 3 grades this line with Tier 2's grader to split driver bugs from model misses (see TRIBAL_KNOWLEDGE.md).
      console.log('[answer] ' + JSON.stringify({ actions: actionList }));
      // A repeated answer is not re-performed: the widget already refused it, and every extra press is
      // behaviour a vendor scores. Re-asking with a fresh sample or a recording is the round's only move.
      if (this.noteAnswer(actionList, retryMode)) {
        // For an animated board that re-ask is only possible if the plan goes: the stored answer is
        // identical by construction, so keeping it would hand the fence the same signature three rounds
        // running. Dropping it sends the next round back to a film that is now a whole round longer.
        this.discardAnimatedPlan();
        return { didInteract: false, tokenUsage: allTokenUsage };
      }
      console.log(`Executing ${actionList.length} actions.`);
      // Stop filming before we touch it. Everything from here to the vendor's verdict is our own answer
      // landing, and a frame of the board wearing our clicks is not a screen the board ever showed.
      this.animatedFilm?.pause();

      for (const action of actionList) {
        if (action.action === ActionKind.CLICK) {
          const c = action as ClickAction;
          const bboxes = c.target_bounding_boxes ?? (c.target_bounding_box ? [c.target_bounding_box] : []);
          if (!bboxes.length && !c.target_coordinates) {
            console.warn('Click action has no bboxes or coordinates', c);
            continue;
          }
          if (bboxes.length) {
            for (const bbox of bboxes) {
              const one = { ...c, target_bounding_box: bbox } as ClickAction;
              if (c.await_keyframe) await this.clickWhenFrameMatches(page, captchaElement, one, requireBox(), c.await_keyframe);
              else await this.executeClick(page, captchaElement, one, requireBox());
              await this.human.pause(PauseKind.BETWEEN);
            }
          } else {
            await this.executeClick(page, captchaElement, c, requireBox());
          }
          performedAction = answered = true;
          await this.emitStep(captchaElement, SolveStage.CLICK, `clicked ${bboxes.length || 1} target(s)`, puzzleSource, frameRole, attempt, { bboxes });
        } else if (action.action === ActionKind.DRAG && !(action as DragAction).source_bounding_box) {
          if (await this.executeSlide(page, captchaElement, scope, action as DragAction, requireBox())) {
            performedAction = slid = true;
            await this.emitStep(captchaElement, SolveStage.DRAG, 'slid the piece into the slot', puzzleSource, frameRole, attempt, { action });
          }
        } else if (action.action === ActionKind.DRAG) {
          const d = action as DragAction;
          if (d.await_keyframe && d.source_bounding_box) await this.waitForKeyframe(captchaElement, d.await_keyframe, ...bboxCenter(d.source_bounding_box));
          await this.executeDrag(page, captchaElement, action as any, requireBox());
          performedAction = answered = true;
          await this.emitStep(captchaElement, SolveStage.DRAG, 'drag', puzzleSource, frameRole, attempt, { action });
        } else if (action.action === ActionKind.TYPE) {
          if (await this.executeType(page, scope, action as TypeAction, widget.at)) {
            performedAction = answered = true;
            await this.emitStep(captchaElement, SolveStage.TYPE, 'typed the code', puzzleSource, frameRole, attempt, { action });
          }
        } else if (action.action === ActionKind.WAIT && (action as any).duration_ms > 0) {
          await delay((action as any).duration_ms);
          performedAction = true;
          await this.emitStep(captchaElement, SolveStage.WAIT, `waited ${(action as any).duration_ms}ms`, puzzleSource, frameRole, attempt, { action });
        }
      }

      // A slide submits itself on release, and any Verify found afterwards belongs to the host page and would
      // submit the guarded form mid-verdict. An empty or `done` plan still presses Verify/Skip.
      const verifyButton = frame || !slid ? await this.getVerifyButton(scope) : null;
      if (!slid && (answered || !performedAction) && verifyButton) {
        console.log(`Clicking Verify to submit (${puzzleSource}).`);
        await this.moveAndClick(page, verifyButton);
        performedAction = true;
        await this.emitStep(captchaElement, SolveStage.SUBMIT, 'submitted (Verify/Next)', puzzleSource, frameRole, attempt);
        this.lastSubmitFrameHash = await this.elementFrameHash(captchaElement);
      }
    } finally {
      await this.releasePendingBurst();
      unlink(screenshotPath);
      if (burstDir && this.animatedPlan?.burstDir !== burstDir) rmdir(burstDir);
    }

    return { didInteract: performedAction, tokenUsage: allTokenUsage };
  }

  private getVerifyButton(scope: Scope): Promise<ElementHandle | null> {
    return this.findControl(scope, SUBMIT_SELECTORS);
  }

  /** Any element at `selector` carrying a non-blank value; response fields are hidden, so no visibility filter. */
  private async hasValue(page: Page, selector: string): Promise<boolean> {
    const fields = await handles(await page.locator(selector).all().catch(() => []));
    return (await Promise.all(fields.map((f) => f.inputValue().catch(() => '')))).some((v) => v.trim().length > 0);
  }

  /** A visible match carrying text; an empty banner or prompt is a placeholder, not a signal. */
  private async visibleWithText(scope: Scope, selector: string): Promise<boolean> {
    return (await Promise.all((await handles(await visible(scope, [selector]))).map(hasText))).some(Boolean);
  }

  private async checkedIn(checkbox: Locator, checked: string): Promise<boolean> {
    const frame = await frameOf(checkbox);
    return !!frame && (await visible(frame, [checked])).length > 0;
  }

  private bannerIsFatalAfterRetry(kind: RecaptchaBanner | null): boolean {
    return kind === RecaptchaBanner.SELECT_MORE || kind === RecaptchaBanner.REJECTED;
  }

  /** Which verdict banner the open challenge shows; `dynamic-more` is the dynamic board's normal flow, not an error. */
  private async bannerKind(page: Page): Promise<RecaptchaBanner | null> {
    const probes = WIDGET_PROBES.filter((p) => p.role === FrameRole.CHALLENGE && SELECTORS[p.vendor].banners);
    const kinds = await Promise.all(probes.map(async (p) => {
      const [at] = await visible(page, [p.selector]);
      const frame = at && await frameOf(at);
      if (!frame) return null;
      const shown = await Promise.all((SELECTORS[p.vendor].banners ?? []).map(async ([sel, kind]) => (await this.visibleWithText(frame, sel)) ? kind : null));
      return shown.find((k) => k !== null) ?? null;
    }));
    return kinds.find((k) => k !== null) ?? null;
  }

  /** The vendor's own done signal: a response token, a painted success state, or a checked box. */
  private async isCaptchaSolved(page: Page): Promise<boolean> {
    try {
      const checkboxes = WIDGET_PROBES.filter((p) => p.role === FrameRole.CHECKBOX && SELECTORS[p.vendor].checked);
      const signals = await Promise.all([
        ...RESPONSE_SELECTORS.map((s) => this.hasValue(page, s)),
        visible(page, ACCEPTED_SELECTORS).then((found) => found.length > 0),
        ...checkboxes.map(async (p) => {
          const [at] = await visible(page, [p.selector]);
          return !!at && this.checkedIn(at, SELECTORS[p.vendor].checked as string);
        }),
      ]);
      return signals.some(Boolean);
    } catch {
      return false;
    }
  }

  /** A next round has painted, as opposed to the answered frame animating closed. */
  private async isChallengeFreshlyRendered(page: Page): Promise<boolean> {
    try {
      const probes = WIDGET_PROBES.filter((p) => p.role === FrameRole.CHALLENGE && SELECTORS[p.vendor].fresh);
      const fresh = await Promise.all(probes.map(async (p) => {
        const [at] = await visible(page, [p.selector]);
        const el = at && await handleOf(at);
        if (!el || (this.lastSubmitFrameHash && (await this.elementFrameHash(el)) === this.lastSubmitFrameHash)) return false;
        const frame = await el.contentFrame();
        return !!frame && this.visibleWithText(frame, SELECTORS[p.vendor].fresh as string);
      }));
      return fresh.some(Boolean);
    } catch {
      return false;
    }
  }

  /**
   * Best-effort: hold until the board's pictures have painted. Nothing to wait for is ready, and so is no prompt: waiting
   * on the prompt as a selector rejects when absent and paid the whole timeout per board.
   */
  private async waitForBoardImages(frame: Frame, s: VendorSelectors): Promise<void> {
    try {
      await frame.waitForFunction(({ prompt, images }: { prompt: string; images: string }) => {
        const vis = (el: Element | null) => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
        const p = prompt && document.querySelector(prompt);
        if (p && !vis(p)) return false;
        return Array.from(document.querySelectorAll(images)).every((el) => {
          if (el instanceof HTMLImageElement) return el.complete && el.naturalWidth > 0;
          const bg = getComputedStyle(el).backgroundImage;
          return !!bg && bg !== 'none' && !/url\(["']?["']?\)/.test(bg);
        });
      }, { prompt: s.fresh ?? '', images: (s.images ?? []).join(',') }, { timeout: this.config.hcaptchaImagesTimeoutMs ?? 3000 });
    } catch { /* timed out or detached mid-load; screenshot anyway */ }
  }

  /** Which vendors' code the page loaded, from resource timing and linked URLs. A tripwire, not a detector. */
  public async vendorsOnTheWire(page: Page): Promise<Vendor[]> {
    let names: string[] = [];
    try {
      const html = await handleOf(page.locator('html'));
      names = html ? await html.evaluate(() => {
        const out: string[] = [];
        try { for (const e of performance.getEntriesByType('resource')) out.push(e.name); } catch (err) { /* buffer unavailable */ }
        for (const el of Array.from(document.querySelectorAll('script[src],iframe[src],link[href],img[src]'))) {
          out.push(el.getAttribute('src') || el.getAttribute('href') || '');
        }
        return out;
      }) : [];
    } catch {
      return [];
    }
    const blob = names.join(' ');
    return VENDORS.filter(([, s]) => s.hosts.some((h) => blob.includes(h))).map(([vendor]) => vendor);
  }

  private async noWidgetMessage(page: Page): Promise<string> {
    const base = 'No interactive captcha widget detected';
    const loaded = await this.vendorsOnTheWire(page);
    if (!loaded.length) {
      return `${base} (no vendor captcha code loaded on this page — likely reCAPTCHA v3 / invisible, or a click-triggered challenge that has not been triggered). Failing fast.`;
    }
    return `${base}, BUT ${loaded.join('/')} code IS loaded and running on this page. The vendor's markup no longer matches anything in SELECTORS — the table needs re-measuring against the vendor's current markup, in both solver ports.`;
  }

  /**
   * Is a widget in the DOM at all, rendered or not? Invisible reCAPTCHA is excluded by its selector. The inline vendors
   * count: without them this port failed fast in under a second on every GeeTest/Yidun page and disagreed with Python in Tier 3.
   */
  public async hasInteractiveWidgetInDom(page: Page): Promise<boolean> {
    return (await Promise.all(WIDGET_PROBES.map((p) => page.locator(p.selector).count().catch(() => 0)))).some((n) => n > 0);
  }

  /** Open challenges first, then unsolved checkboxes, then the inline vendors; every probe is queried at once. */
  public async detectCaptcha(page: Page): Promise<Widget | null> {
    const found = await Promise.all(WIDGET_PROBES.map(async (p) => {
      const [at] = await visible(page, [p.selector]);
      return at && !(await this.alreadyAccepted(page, at, p)) ? { ...p, at } : null;
    }));
    const hit = found.find((h) => h !== null);
    const el = hit && await handleOf(hit.at);
    return hit && el ? { el, at: hit.at, vendor: hit.vendor, role: hit.role } : null;
  }

  /** A checkbox the vendor has already accepted is not a captcha to solve. */
  private async alreadyAccepted(page: Page, at: Locator, { role, vendor }: WidgetProbe): Promise<boolean> {
    const { response, checked } = SELECTORS[vendor];
    if (role !== FrameRole.CHECKBOX) return false;
    const [token, box] = await Promise.all([!!response && this.hasValue(page, response), !!checked && this.checkedIn(at, checked)]);
    return token || box;
  }

  private loraName(cliRoot: string): string {
    if (this.loraNameCache === null) this.loraNameCache = resolveLoraName({ cliRoot, baseUrl: process.env.VLLM_BASE_URL });
    return this.loraNameCache;
  }

  /** The `--model` argv, or nothing: without VLLM_BASE_URL the CLI can see a credentials file this process cannot. */
  private modelName(cliRoot: string): string | undefined {
    const explicit = this.config.model ?? process.env.CAPTCHA_LORA_NAME;
    if (explicit) return explicit;
    return process.env.VLLM_BASE_URL ? this.loraName(cliRoot) : undefined;
  }

  private resolveCli(): { cliRoot: string; py: string } {
    if (this.cliCache) return this.cliCache;
    const { repoPath, pythonCommand } = this.config;
    const cliRoot = repoPath ?? getBundledCliRoot();
    if (!fs.existsSync(cliRoot)) {
      throw new Error(`CaptchaKraken CLI folder not found at ${cliRoot}. If you installed from npm, ensure the package ships 'python/'.`);
    }
    const py = resolvePythonCommand({ configured: pythonCommand, venvPython: getVenvPython(cliRoot), exists: commandExists });
    this.cliCache = { cliRoot, py };
    return this.cliCache;
  }

  /** One-shot CLI tool call; `{}` on any failure so polling callers keep going. */
  private async runCliTool(args: string[]): Promise<any> {
    try {
      const { cliRoot, py } = this.resolveCli();
      const { stdout } = await execFileAsync(py, ['-m', 'captchakraken.cli', ...args], { cwd: cliRoot, env: cliEnv(cliRoot), maxBuffer: 10 * 1024 * 1024 });
      return JSON.parse(stdout.trim());
    } catch {
      return {};
    }
  }

  /** Start the persistent CV worker once; false when it cannot start, so callers fall back to one-shot spawns. */
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
            const p = typeof msg.id === 'number' ? this.cvWorkerPending.get(msg.id) : undefined;
            if (p) {
              this.cvWorkerPending.delete(msg.id);
              if (msg.ok) p.resolve(msg.result);
              else p.reject(new Error(msg.error || 'cv worker error'));
            }
          }
        });
        proc.on('error', fail);
        proc.on('exit', () => {
          for (const [, p] of this.cvWorkerPending) p.reject(new Error('cv worker exited'));
          this.cvWorkerPending.clear();
          fail();
        });
        setTimeout(() => { if (!settled) { settled = true; resolve(false); } }, 8000);
      } catch {
        resolve(false);
      }
    });
    return this.cvWorkerReady;
  }

  private cvWorkerRequest(payload: Record<string, any>, timeoutMs = 10000): Promise<any> {
    const proc = this.cvWorker;
    if (!proc || proc.exitCode !== null) return Promise.reject(new Error('cv worker not running'));
    const id = ++this.cvWorkerSeq;
    return new Promise<any>((resolve, reject) => {
      const timer = setTimeout(() => { if (this.cvWorkerPending.delete(id)) reject(new Error('cv worker request timeout')); }, timeoutMs);
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

  private teardownCvWorker(): void {
    const proc = this.cvWorker;
    this.cvWorker = null;
    if (proc) { try { proc.kill(); } catch { /* best-effort */ } }
  }

  /** A CV tool through the worker when it is up, else a one-shot subprocess. Never throws. */
  private async runCvTool(cmd: string, payload: Record<string, any>, fallbackArgs: string[]): Promise<any> {
    try {
      if (await this.ensureCvWorker()) return await this.cvWorkerRequest({ cmd, ...payload });
    } catch { /* fall through to one-shot */ }
    return this.runCliTool(fallbackArgs);
  }

  /** A still board is not a loaded board: hold until the panel's centre carries structure. Returns how long it waited. */
  private async waitForBoardPainted(el: ElementHandle, opts?: { pollMs?: number; timeoutMs?: number; floor?: number }): Promise<{ verdict: PaintVerdict; waitedMs: number }> {
    const pollMs = opts?.pollMs ?? this.config.boardPaintPollMs ?? 180;
    const timeout = opts?.timeoutMs ?? this.config.boardPaintTimeoutMs ?? 2500;
    const floor = opts?.floor ?? this.config.boardPaintFloor;
    const start = Date.now();
    let saw: PaintVerdict = PaintVerdict.UNKNOWN;
    for (;;) {
      const f = tmp('paint');
      let got = false;
      try {
        await this.shot(el, f);
        got = true;
        const res = await this.runCvTool('board-painted', floor === undefined ? { image: f } : { image: f, floor },
          floor === undefined ? ['board-painted', f] : ['board-painted', f, String(floor)]);
        saw = res?.painted === true ? PaintVerdict.PAINTED : res?.painted === false ? PaintVerdict.BLANK : PaintVerdict.UNKNOWN;
      } catch { /* a failed grab is a skipped poll, not a verdict */ }
      unlink(f);
      if (got && saw !== PaintVerdict.BLANK) break;
      if (Date.now() - start >= timeout) {
        if (saw === PaintVerdict.BLANK) console.log(`[board] the widget never painted a puzzle in ${timeout}ms — photographing the panel as it is`);
        break;
      }
      await delay(pollMs);
    }
    const waitedMs = Date.now() - start;
    if (saw === PaintVerdict.PAINTED && waitedMs >= pollMs) console.log(`[board] waited ${waitedMs}ms for the widget to paint its puzzle`);
    return { verdict: saw, waitedMs };
  }

  /**
   * Screenshot `el` every `intervalMs` until `judge(lastTwoFrames)` answers, or the time is up.
   * A screenshot that fails ends the poll: the widget is gone or going.
   */
  private async pollFrames<T>(el: ElementHandle, timeoutMs: number, intervalMs: number,
                              judge: (frames: string[]) => Promise<T | undefined>, before?: () => Promise<void>): Promise<T | undefined> {
    const start = Date.now();
    const frames: string[] = [];
    try {
      while (Date.now() - start < timeoutMs) {
        const t0 = Date.now();
        if (before) await before();
        const f = tmp('poll');
        try {
          await this.shot(el, f, Math.max(500, Math.min(2500, timeoutMs - (Date.now() - start))));
        } catch {
          return undefined;
        }
        frames.push(f);
        const verdict = await judge(frames);
        if (verdict !== undefined) return verdict;
        if (frames.length > 1) unlink(frames.shift());
        await delay(Math.max(0, intervalMs - (Date.now() - t0)));
      }
      return undefined;
    } finally {
      for (const f of frames) unlink(f);
    }
  }

  private async waitForGridCellsLoaded(captchaElement: ElementHandle, opts?: { intervalMs?: number; timeoutMs?: number }): Promise<boolean> {
    const interval = opts?.intervalMs ?? this.config.gridLoadPollIntervalMs ?? 250;
    const timeout = opts?.timeoutMs ?? this.config.gridLoadTimeoutMs ?? 8000;
    return (await this.pollFrames(captchaElement, timeout, interval, async (frames) => {
      if (frames.length < 2) return undefined;
      const res = await this.runCvTool('grid-cell-states', { a: frames[0], b: frames[1] }, ['grid-cell-states', frames[0], frames[1]]);
      const ok = res && res.grid !== null && Array.isArray(res.loaded) && res.loaded.length > 0
        && Array.isArray(res.empty) && res.empty.length === 0 && Array.isArray(res.changing) && res.changing.length === 0;
      return ok ? true : undefined;
    })) === true;
  }

  private async getGridBoxes(captchaElement: ElementHandle): Promise<{ boxes: number[][]; size: 3 | 4; screenshotW: number; screenshotH: number } | null> {
    const f = tmp('findgrid');
    try {
      await this.shot(captchaElement, f);
      const res = await this.runCvTool('find-grid', { image: f }, ['find-grid', f]);
      const dims = readPngDimensions(f);
      if (!Array.isArray(res) || (res.length !== 9 && res.length !== 16) || !dims) return null;
      return { boxes: res as number[][], size: res.length === 16 ? 4 : 3, screenshotW: dims.width, screenshotH: dims.height };
    } catch {
      return null;
    } finally {
      unlink(f);
    }
  }

  private bboxToCell(bbox: [number, number, number, number], gridBoxes: number[][], screenshotW: number, screenshotH: number): number | null {
    const cx = ((bbox[0] + bbox[2]) / 2) * screenshotW;
    const cy = ((bbox[1] + bbox[3]) / 2) * screenshotH;
    const i = gridBoxes.findIndex(([x1, y1, x2, y2]) => cx >= x1 && cx <= x2 && cy >= y1 && cy <= y2);
    return i < 0 ? null : i + 1;
  }

  private cellCenterPage(cell: number, session: GridSession): { x: number; y: number } {
    const [x1, y1, x2, y2] = session.gridBoxes[cell - 1];
    return { x: session.elementBox.x + (x1 + x2) / 2 * session.scaleX, y: session.elementBox.y + (y1 + y2) / 2 * session.scaleY };
  }

  private async hoverCell(page: Page, session: GridSession, cell: number): Promise<void> {
    if (!this.human.hovers) return;
    const [x1, y1, x2, y2] = session.gridBoxes[0];
    const center = this.cellCenterPage(cell, session);
    await this.performSmoothMove(page,
      center.x + (Math.random() - 0.5) * (x2 - x1) * session.scaleX * 0.4,
      center.y + (Math.random() - 0.5) * (y2 - y1) * session.scaleY * 0.4);
  }

  /** Per-cell state with the session's cached boxes: a blank mid-refresh frame has no lattice to detect. */
  private async gridCellStates(session: GridSession, frameA: string, frameB: string): Promise<GridCellStates | null> {
    const res = await this.runCvTool('grid-cell-states-fixed', { a: frameA, b: frameB, grid_boxes: session.gridBoxes },
      ['grid-cell-states-fixed', frameA, frameB, JSON.stringify(session.gridBoxes)]);
    if (!res || !Array.isArray(res.empty)) return null;
    return { empty: res.empty ?? [], changing: res.changing ?? [], loaded: res.loaded ?? [], selected: res.selected ?? [] };
  }

  private orderByPriority(loading: number[], priority: number[]): number[] {
    const set = new Set(loading);
    const ordered = priority.filter((c) => set.delete(c));
    return [...ordered, ...set];
  }

  private hoverLoop(page: Page, session: GridSession, cells: number[]): (() => Promise<void>) | undefined {
    if (!cells.length) return undefined;
    let i = 0;
    return async () => { await this.hoverCell(page, session, cells[i++ % cells.length]).catch(() => {}); };
  }

  /**
   * `chipped`: the photos were kept, press Verify. `loading`: those tiles are being swapped, read the board again.
   * Chip is tested first: a chip zooms the photo, which reads as `changing` on the very frame that shows it.
   */
  private async watchClickedTiles(page: Page, captchaElement: ElementHandle, session: GridSession, priority: number[] = []): Promise<{ loading: number[]; chipped: boolean }> {
    const grace = this.config.recaptchaFadeOnsetGraceMs ?? 4000;
    const interval = this.config.recaptchaDynamicFadePollMs ?? 250;
    const watch = priority.length ? new Set(priority) : null;
    const verdict = await this.pollFrames(captchaElement, grace, interval, async (frames) => {
      if (frames.length < 2) return undefined;
      const st = await this.gridCellStates(session, frames[0], frames[1]);
      if (priority.length && priority.every((c) => (st?.selected ?? []).includes(c))) return { loading: [] as number[], chipped: true };
      const loading = [...new Set([...(st?.empty ?? []), ...(st?.changing ?? [])].filter((c) => !watch || watch.has(c)))];
      return loading.length ? { loading: this.orderByPriority(loading, priority), chipped: false } : undefined;
    }, this.hoverLoop(page, session, priority));
    return verdict ?? { loading: [], chipped: false };
  }

  private async waitForAnyClickedTileLoaded(page: Page, captchaElement: ElementHandle, session: GridSession, fadingCells: number[]): Promise<boolean> {
    if (!fadingCells.length) return true;
    const hover = (this.config.recaptchaTileHoverEnabled ?? true) ? this.hoverLoop(page, session, fadingCells) : undefined;
    return (await this.pollFrames(captchaElement, this.config.recaptchaDynamicFadeWaitMs ?? 6000, this.config.recaptchaDynamicFadePollMs ?? 250,
      async (frames) => {
        if (frames.length < 2) return undefined;
        const st = await this.gridCellStates(session, frames[0], frames[1]);
        return st && fadingCells.some((c) => st.loaded.includes(c)) ? true : undefined;
      }, hover)) === true;
  }

  /** Click, watch the clicked tiles, re-solve while they swap, submit on `done` or a chipped board. */
  private async solveRecaptchaGrid(page: Page, captchaElement: ElementHandle, attempt: number, retryMode: RetryMode | null,
                                   grid: { boxes: number[][]; size: 3 | 4; screenshotW: number; screenshotH: number },
                                   elementBox: Box): Promise<{ didInteract: boolean; tokenUsage: TokenUsage[] }> {
    const maxRounds = this.config.recaptchaMaxDynamicRounds ?? DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS;
    const session: GridSession = { gridBoxes: grid.boxes, elementBox, scaleX: elementBox.width / grid.screenshotW,
      scaleY: elementBox.height / grid.screenshotH, screenshotW: grid.screenshotW, screenshotH: grid.screenshotH };
    const clickedOrder: number[] = [];
    let performedAction = false;
    let shouldSubmit = false;
    const allTokenUsage: TokenUsage[] = [];
    let pendingRetry = retryMode;

    for (let round = 1; round <= maxRounds; round++) {
      if (round > 1) await this.ph(Phase.GRID_LOAD, () => this.waitForGridCellsLoaded(captchaElement));
      const shotA = tmp('recap');
      try {
        await this.ph(Phase.SCREENSHOT, () => this.shot(captchaElement, shotA));
      } catch {
        break;
      }
      await this.emitStep(captchaElement, round === 1 ? SolveStage.INITIAL : SolveStage.ROUND, `round-${round}:pre-solve`, Vendor.RECAPTCHA, FrameRole.CHALLENGE, attempt, { round });

      let action: CaptchaAction | null = null;
      try {
        const retryForThisRound = pendingRetry;
        pendingRetry = null;
        const response = await this.ph(Phase.INFERENCE, () => this.solveFrameFreshnessGuarded(captchaElement, shotA,
          (imagePath) => this.getSolution(imagePath, Vendor.RECAPTCHA, retryForThisRound)));
        allTokenUsage.push(...response.token_usage);
        action = (Array.isArray(response.actions) ? response.actions : [response.actions])[0] ?? null;
      } finally {
        unlink(shotA);
      }

      if (!action || action.action === ActionKind.DONE) {
        console.log(`[recaptcha-grid] round ${round}: done; submitting.`);
        shouldSubmit = true;
        break;
      }
      if (action.action === ActionKind.WAIT) {
        await this.ph(Phase.FADE_WAIT, async () => {
          const { loading } = await this.watchClickedTiles(page, captchaElement, session, clickedOrder);
          await this.waitForAnyClickedTileLoaded(page, captchaElement, session, loading);
        });
        continue;
      }
      if (action.action !== ActionKind.CLICK) {
        console.warn(`[recaptcha-grid] round ${round}: unexpected action '${(action as any).action}'; re-solving.`);
        continue;
      }
      const c = action as ClickAction;
      const bboxes = c.target_bounding_boxes ?? (c.target_bounding_box ? [c.target_bounding_box] : []);
      if (!bboxes.length) {
        await delay(500);
        continue;
      }
      const clickedThisRound: number[] = [];
      for (const bbox of bboxes) {
        const cell = this.bboxToCell(bbox, session.gridBoxes, session.screenshotW, session.screenshotH);
        await this.executeClick(page, captchaElement, { action: ActionKind.CLICK, target_bounding_box: bbox } as ClickAction, elementBox);
        if (cell != null) { clickedOrder.push(cell); clickedThisRound.push(cell); }
        await this.human.pause(PauseKind.BETWEEN);
      }
      performedAction = true;
      console.log(`[recaptcha-grid] round ${round}: clicked ${bboxes.length} tile(s) -> cells ${JSON.stringify(clickedThisRound)}.`);
      await this.emitStep(captchaElement, SolveStage.CLICK, `round-${round}:clicked ${bboxes.length} tile(s)`, Vendor.RECAPTCHA, FrameRole.CHALLENGE, attempt, { round, clickedThisRound, bboxes });

      const { loading, chipped } = await this.ph(Phase.FADE_WAIT, () => this.watchClickedTiles(page, captchaElement, session, clickedThisRound));
      if (chipped || !loading.length) {
        console.log(`[recaptcha-grid] round ${round}: ${chipped ? 'tiles chipped' : 'no tiles loading'} after click; submitting.`);
        shouldSubmit = true;
        break;
      }
      await this.ph(Phase.FADE_WAIT, () => this.waitForAnyClickedTileLoaded(page, captchaElement, session, loading));
    }

    if (shouldSubmit) {
      const frame = await captchaElement.contentFrame();
      const verifyButton = frame && await this.getVerifyButton(frame);
      if (verifyButton) {
        console.log('[recaptcha-grid] clicking Verify to submit.');
        await this.moveAndClick(page, verifyButton);
        performedAction = true;
        await this.emitStep(captchaElement, SolveStage.SUBMIT, 'submitted (Verify)', Vendor.RECAPTCHA, FrameRole.CHALLENGE, attempt);
        this.lastSubmitFrameHash = await this.elementFrameHash(captchaElement).catch(() => null);
      }
    }
    return { didInteract: performedAction, tokenUsage: allTokenUsage };
  }

  /**
   * Start filming now and decide later what it was for: the classifier, the speculative film, or the
   * recording an animated answer is sliced from. Every window is wall-clock. A cycle is a screen that
   * comes back; a board that shows nothing new for a floor window has settled. There is deliberately no
   * "enough screens, stop" exit: measured on number_with_highest_value_video it failed every seed either way.
   *
   * `continuous` keeps the camera rolling past the first answer, for the whole solve. A one-shot burst can
   * only ever show the model the screens that happened to fall inside its window — measured, a 4s burst
   * against a 5.3s cycle showed two screens of three, and when the answer lived on the third there was no
   * second chance, because the reused answer is identical by construction. Filming on means every later
   * round slices a STRICTLY LONGER film: more repetitions for `_detect_cycle` to confirm against, and, on a
   * board that never repeats, six evenly-spread picks across the whole solve instead of across four seconds.
   */
  private startKeyframeBurst(captchaElement: ElementHandle, continuous = false): {
    moved: () => boolean;
    screensSeen: () => number;
    stableFrame: () => string | null;
    ready: () => Promise<SettleVerdict>;
    verdict: () => Promise<boolean>;
    settledOrCycled: () => Promise<void>;
    pause: () => void;
    resume: () => void;
    snapshot: () => Promise<{ dir: string; moved: boolean }>;
    abandon: () => Promise<void>;
    finish: () => Promise<{ dir: string; moved: boolean }>;
  } {
    const cfg = this.config;
    const fps = Math.max(1, cfg.videoBurstFps ?? 10);
    const floorMs = cfg.videoBurstDurationMs ?? 4000;
    // A continuous film ends with the solve, not on its own clock. The ceiling stays as a safety net so a
    // widget that never stops changing cannot fill a disk; it is not the working limit.
    const ceilingMs = continuous
      ? Math.max(floorMs, cfg.videoFilmMaxMs ?? 120_000)
      : Math.max(floorMs, cfg.videoBurstMaxMs ?? 12_000);
    const intervalMs = 1000 / fps;
    const t0 = Date.now();
    let nextAt = t0;
    const elapsed = () => Date.now() - t0;
    const hangDeadline = Date.now() + burstHangDeadlineMs(cfg);
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ck_burst_'));
    const order: string[] = [];
    let captured = 0;
    let lastDigest: string | null = null;
    let lastNewMs = 0;
    let lastChangeMs = 0;
    let lastFrame: string | null = null;
    let cycleClosed = false;
    let stopped = false;
    let runToEnd = false;
    let paused = false;
    let hangAt = hangDeadline;
    // Names are contiguous across a pause and across a dropped frame, because the slicer sorts by name and
    // spaces what it finds evenly. A gap in the numbering would tell it the board held a screen it never held.
    let seq = 0;

    const loop = (async () => {
      for (let i = 0; elapsed() < ceilingMs && !stopped; i++) {
        if (paused) {
          // The camera stays alive but films nothing: the next frames would be OUR answer landing, and a
          // board carrying our own clicks is not a screen the board ever showed on its own.
          await delay(intervalMs);
          nextAt = Date.now();
          hangAt = Date.now() + burstHangDeadlineMs(cfg);
          continue;
        }
        if (Date.now() > hangAt) {
          console.warn(`[animated] the recording stalled: ${captured} frames in ${burstHangDeadlineMs(cfg)}ms — the widget is not screenshotting`);
          break;
        }
        const frame = path.join(dir, `frame_${String(seq).padStart(4, '0')}.png`); // zero-padded: the slicer sorts by name
        try {
          await this.shot(captchaElement, frame, cfg.elementScreenshotTimeoutMs ?? 8000, 'allow');
          captured++;
          seq++;
          lastFrame = frame;
          try {
            const d = sha1(frame);
            if (d !== lastDigest) {
              lastChangeMs = elapsed();
              if (order.includes(d) && order.length >= 2) cycleClosed = true;
              else if (!order.includes(d)) { order.push(d); lastNewMs = elapsed(); }
              lastDigest = d;
            }
          } catch { /* no digest, no early stop */ }
        } catch { /* a dropped frame costs a sample, not the recording */ }
        // A continuous film reaches these same conditions and keeps rolling: they are what makes a SNAPSHOT
        // ready to slice, not what makes the recording over. `settledOrCycled()` reads them without stopping.
        const elapsedMs = elapsed();
        if (!continuous && runToEnd && elapsedMs >= floorMs && elapsedMs - lastNewMs >= floorMs) {
          console.log(`[animated] no new screen for ${(floorMs / 1000).toFixed(1)}s (${order.length} seen) — the board has settled; stopping the burst`);
          break;
        }
        if (!continuous && runToEnd && cycleClosed && elapsedMs >= floorMs) {
          console.log(`[animated] cycle closed after ${(elapsedMs / 1000).toFixed(1)}s (${order.length} screens); stopping the burst`);
          break;
        }
        // Sleep to a fixed grid, not `interval - work`: per-frame overshoot would otherwise accumulate and a
        // loaded runner films fewer frames than the floor window holds. A stalled frame skips, not bunches.
        nextAt = Math.max(nextAt + intervalMs, Date.now());
        const wait = nextAt - Date.now();
        if (wait > 0 && nextAt - t0 < ceilingMs) await delay(wait);
      }
    })();
    let ended = false;
    loop.then(() => { ended = true; }, () => { ended = true; });

    return {
      moved: () => cycleClosed,
      screensSeen: () => order.length,
      stableFrame: () => lastFrame,

      /** Static once the picture has held for the settle window; animated once a screen came back or enough arrived. */
      ready: async () => {
        const settleWindowMs = Math.max(2 * intervalMs, (cfg.settleFrames ?? 2) * (cfg.settlePollMs ?? 220));
        const earlyScreens = Math.max(2, cfg.animatedMotionStreak ?? 5);
        for (;;) {
          if (cycleClosed) { console.log('[animated] a screen came back — recording it'); return SettleVerdict.ANIMATED; }
          if (order.length >= earlyScreens) {
            console.log(`[animated] ${order.length} screens in ${(elapsed() / 1000).toFixed(1)}s and still arriving — recording it`);
            return SettleVerdict.ANIMATED;
          }
          if (elapsed() >= settleWindowMs && elapsed() - lastChangeMs >= settleWindowMs) return SettleVerdict.SETTLED;
          if (ended || stopped) return order.length > 1 ? SettleVerdict.ANIMATED : SettleVerdict.SETTLED;
          await delay(intervalMs);
        }
      },

      /** Wait until the recording can answer "is it cycling": a closed cycle, a settled board, or endless new screens. */
      verdict: async () => {
        let animating = false;
        let why = 'a screen came back';
        while (!cycleClosed && !ended && !stopped) {
          const elapsedMs = elapsed();
          if (elapsedMs >= floorMs) {
            if (elapsedMs - lastNewMs >= floorMs) { why = 'no new screen for a full floor window — it moved once and settled'; break; }
            if (order.length > BURST_ANIMATED_SCREENS) { animating = true; why = `${order.length} screens and still arriving — animating continuously`; break; }
          }
          await delay(intervalMs);
        }
        const moved = cycleClosed || animating;
        if (!moved && (ended || stopped)) why = ended ? 'the recording ended' : 'abandoned';
        console.log(`[animated] burst verdict after ${(elapsed() / 1000).toFixed(1)}s: ${moved ? 'ANIMATED' : 'still'} (${order.length} screens; ${why})`);
        return moved;
      },

      /**
       * Resolve once the film holds something worth slicing: a closed cycle, or a board that has shown
       * nothing new for a floor window. The same two conditions `finish()` stops on — read, not acted on,
       * so a continuous film can be sliced repeatedly without ever being ended.
       */
      settledOrCycled: async () => {
        runToEnd = true;
        while (!ended && !stopped) {
          const elapsedMs = elapsed();
          if (elapsedMs >= floorMs && (cycleClosed || elapsedMs - lastNewMs >= floorMs)) return;
          await delay(intervalMs);
        }
      },

      pause: () => { paused = true; },
      resume: () => { paused = false; nextAt = Date.now(); hangAt = Date.now() + burstHangDeadlineMs(cfg); },

      /**
       * A stable copy of everything filmed so far, WITHOUT ending the recording.
       *
       * Hardlinked rather than copied: the slicer wants a directory it can sort by name and read to the end,
       * and handing it the live one would have it read a directory being written underneath it — a frame
       * half-flushed reads as a corrupt PNG, and a frame arriving mid-sort shifts every index after it.
       * Links are near-free on the same filesystem, which is why this can be afforded once a round.
       */
      snapshot: async () => {
        if (cfg.videoSolveEnabled !== false && !this.videoBudgetGranted) {
          this.videoBudgetGranted = true;
          this.videoBudgetMs = (cfg.videoBurstMaxMs ?? 12_000) + (cfg.keyframeWaitTimeoutMs ?? SOLVE_DEFAULTS.keyframeWaitTimeoutMs) + (cfg.videoExtraInferenceMs ?? 8000);
        }
        const names = fs.readdirSync(dir).filter((f) => f.endsWith('.png')).sort();
        if (!names.length) {
          const e: any = new Error('ANIMATED_CHALLENGE: could not record the animated challenge (no frame screenshotted).');
          e.animated = true;
          throw e;
        }
        const out = fs.mkdtempSync(path.join(os.tmpdir(), 'ck_slice_'));
        // The LAST name can be the frame currently being written. Dropping it costs one sample and removes
        // the only torn read this can have.
        for (const n of names.slice(0, -1)) {
          try { fs.linkSync(path.join(dir, n), path.join(out, n)); } catch { fs.copyFileSync(path.join(dir, n), path.join(out, n)); }
        }
        const burstMs = Math.max(1, elapsed());
        this.lastBurstFps = captured / (burstMs / 1000);
        const animating = order.length > BURST_ANIMATED_SCREENS && burstMs - lastNewMs < floorMs;
        console.log(`[animated] sliced ${names.length - 1} of ${captured} frames filmed over ${(burstMs / 1000).toFixed(1)}s (${order.length} screens) -> ${out}`);
        return { dir: out, moved: cycleClosed || animating };
      },

      abandon: async () => {
        stopped = true;
        try { await loop; } catch { /* the recording never fails a solve */ }
        rmdir(dir);
      },

      /** Run to the end of the cycle; the escalation buys its own budget once per solve. */
      finish: async () => {
        runToEnd = true;
        if (cfg.videoSolveEnabled !== false && !this.videoBudgetGranted) {
          this.videoBudgetGranted = true;
          this.videoBudgetMs = (cfg.videoBurstMaxMs ?? 12_000) + (cfg.keyframeWaitTimeoutMs ?? SOLVE_DEFAULTS.keyframeWaitTimeoutMs) + (cfg.videoExtraInferenceMs ?? 8000);
        }
        try { await loop; } catch { /* fall through to the captured-count check */ }
        if (!captured) {
          rmdir(dir);
          const e: any = new Error('ANIMATED_CHALLENGE: could not record the animated challenge (no frame screenshotted).');
          e.animated = true;
          throw e;
        }
        const burstMs = Math.max(1, elapsed());
        this.lastBurstFps = captured / (burstMs / 1000);
        const animating = order.length > BURST_ANIMATED_SCREENS && burstMs - lastNewMs < floorMs;
        console.log(`[animated] recorded ${captured} frames in ${(burstMs / 1000).toFixed(1)}s (${this.lastBurstFps.toFixed(1)}fps) -> ${dir}`);
        return { dir, moved: cycleClosed || animating };
      },
    };
  }

  /** Slice a burst into keyframes and solve them in one model request. The frame number is the guard. */
  private async getAnimatedSolution(framesDir: string): Promise<CliResponse> {
    const { cliRoot, py } = this.resolveCli();
    const { apiKey = process.env.CAPTCHA_KRAKEN_API_KEY ?? process.env.VLLM_API_KEY } = this.config;
    const m = this.modelName(cliRoot);
    const args = ['-m', 'captchakraken.cli', 'solve-animated', '--frames-dir', framesDir,
      '--fps', String(this.lastBurstFps ?? this.config.videoBurstFps ?? 10), ...(m ? ['--model', m] : [])];
    try {
      const { stdout, stderr } = await execFileAsync(py, args, { cwd: cliRoot, env: this.solveEnvironment(cliRoot, apiKey), maxBuffer: 10 * 1024 * 1024 });
      if (stderr) console.error('CaptchaKraken CLI stderr:', stderr);
      const parsed = JSON.parse(stdout.trim());
      if (parsed.keyframe_mode != null && !isOneOf(KeyframeMode, parsed.keyframe_mode)) throw new Error(`engine reported an unknown keyframe_mode '${parsed.keyframe_mode}'`);
      this.keyframeMode = parsed.keyframe_mode ?? null;
      this.keyframeSteadyScreens = parsed.steady_screens ?? 0;
      console.log(`[animated] ${parsed.source_frames} frames -> ${(parsed.keyframes ?? []).length} keyframe(s) (mode=${parsed.keyframe_mode})`);
      return { actions: parsed.actions ?? [], token_usage: parsed.token_usage ?? [] };
    } catch (error: any) {
      throw this.cliError(error, 'Cannot solve this animated captcha', 'Failed to execute the animated captcha solver');
    }
  }

  /** The session id and the credential travel in the environment, never in argv. */
  private solveEnvironment(cliRoot: string, apiKey: string | undefined): NodeJS.ProcessEnv {
    return solveEnv(cliEnv(cliRoot, this.solveSessionId ? { CAPTCHA_KRAKEN_SESSION: this.solveSessionId } : undefined), apiKey, this.resampleLevel);
  }

  /** Exit 2 is an unsupported puzzle, exit 3 the hosted API's own refusal; both are relayed, not reworded. */
  private cliError(error: any, unsupportedMessage: string, failMessage: string): Error {
    const stderr: string = error.stderr ?? '';
    if (/"unsupported"\s*:\s*true/.test(stderr)) {
      const e = new Error(`UNSUPPORTED_CAPTCHA: ${unsupportedMessage}`);
      (e as any).unsupported = true;
      return e;
    }
    const apiError = parseApiError(stderr);
    if (apiError) return apiError;
    console.error(`${failMessage}:`, error);
    if (error.stdout) console.log('CLI stdout on error:', error.stdout);
    if (error.stderr) console.error('CLI stderr on error:', error.stderr);
    return new Error(`${failMessage}: ${error.message}`);
  }

  /** Does the chosen keyframe's answer area appear in a sibling keyframe of the same clip? */
  private async answerRegionRecurs(keyframePath: string, cx: number, cy: number): Promise<boolean> {
    let siblings: string[] = [];
    try {
      const dir = path.dirname(keyframePath);
      const base = path.basename(keyframePath);
      const prefix = base.replace(/_\d+\.png$/i, '_');
      if (prefix === base) return false;
      siblings = fs.readdirSync(dir).filter((f) => f.startsWith(prefix) && f.toLowerCase().endsWith('.png')).map((f) => path.join(dir, f)).filter((f) => f !== keyframePath);
    } catch {
      return false;
    }
    for (const other of siblings) {
      const r = await this.runCvTool('match-region', { ref: keyframePath, live: other, cx, cy }, ['match-region', keyframePath, other, String(cx), String(cy)]);
      if (r && r.match === true) return true;
    }
    return false;
  }

  /** Hold until the widget looks like the keyframe around the action point, or the wait is spent. */
  private async waitForKeyframe(captchaElement: ElementHandle, keyframePath: string, cx: number, cy: number): Promise<boolean> {
    const steady = this.keyframeSteadyScreens >= 2;
    if (!steady && !(await this.answerRegionRecurs(keyframePath, cx, cy))) {
      console.log(`[animated] clip sits on ${this.keyframeSteadyScreens} steady screen(s) and the answer area is unique to the chosen frame; acting on it without waiting`);
      return false;
    }
    const fullTimeout = this.config.keyframeWaitTimeoutMs ?? SOLVE_DEFAULTS.keyframeWaitTimeoutMs;
    const timeout = steady ? fullTimeout : Math.min(fullTimeout, this.config.videoBurstDurationMs ?? 4000);
    const interval = this.config.keyframeWaitPollMs ?? 120;
    const deadline = Math.min(Date.now() + timeout, this.solveDeadlineAt || Number.MAX_SAFE_INTEGER);
    const probe = tmp('ck_kfwait');
    let polls = 0;
    let best = 1;
    try {
      while (Date.now() < deadline) {
        try {
          await this.shot(captchaElement, probe, this.config.elementScreenshotTimeoutMs ?? 8000, 'allow');
          const r = await this.runCvTool('match-region', { ref: keyframePath, live: probe, cx, cy }, ['match-region', keyframePath, probe, String(cx), String(cy)]);
          if (typeof r?.diff === 'number') best = Math.min(best, r.diff);
          if (r?.match) {
            console.log(`[animated] widget matched the chosen keyframe (diff=${r.diff.toFixed(4)})`);
            return true;
          }
          polls++;
          if (polls >= NOT_THIS_BOARD_POLLS && best > NOT_THIS_BOARD_DIFF) {
            console.log(`[animated] the widget no longer resembles the recorded board (best diff=${best.toFixed(4)} over ${polls} polls); not waiting out the budget`);
            this.discardAnimatedPlan();
            await this.stopAnimatedFilm();
            return false;
          }
        } catch { /* a failed probe is one lost poll */ }
        await delay(interval);
      }
    } finally {
      unlink(probe);
    }
    this.discardAnimatedPlan();
    await this.stopAnimatedFilm();
    console.log(`[animated] widget never matched the chosen keyframe within ${timeout}ms (closest diff=${best.toFixed(4)}); clicking on the model's coordinates anyway, and recording afresh next round`);
    return false;
  }

  private resetSolveState(): void {
    this.solutionCache.clear();
    this.repeatedAnswerSeen = false;
    this.knownAnimated = false;
    this.animatedProbeDone = false;
    this.discardAnimatedPlan();
    this.lastSubmitFrameHash = null;
    this.keyframeMode = null;
    this.keyframeSteadyScreens = 0;
    this.solveDeadlineAt = 0;
    this.lastAnswerSig = null;
    this.noProgressRounds = 0;
    this.resampleLevel = 0;
    this.videoBudgetMs = 0;
    this.videoBudgetGranted = false;
  }

  /** Keyed on the retry mode too: the missed-tiles answer legitimately overlaps the previous one. */
  private static answerSignature(actions: any[], retryMode: RetryMode | null): string | null {
    const round3 = (v: any): any => typeof v === 'number' ? Math.round(v * 1000) / 1000 : Array.isArray(v) ? v.map(round3) : v ?? null;
    try {
      return JSON.stringify([retryMode ?? null, actions.map((a: any) => [a?.action ?? null, round3(a?.target_bounding_boxes),
        round3(a?.target_bounding_box), round3(a?.target_coordinates), round3(a?.source_bounding_box), a?.text ?? null])]);
    } catch {
      return null;
    }
  }

  /** True when this answer already ran and changed nothing: resample, and let the recording path have a go. */
  private noteAnswer(actions: any[], retryMode: RetryMode | null): boolean {
    const sig = CaptchaKrakenSolver.answerSignature(actions, retryMode);
    if (sig !== null && sig === this.lastAnswerSig) {
      this.noProgressRounds++;
      console.log(`[no-progress] the model returned the same answer again (${this.noProgressRounds}/${this.config.maxNoProgressRounds ?? 2}) — the previous one already ran and changed nothing`);
      this.resampleLevel++;
      this.repeatedAnswerSeen = true;
      return true;
    }
    this.noProgressRounds = 0;
    this.lastAnswerSig = sig;
    return false;
  }

  /** A cache hit costs no inference, and means the answer already ran: the board cycles. */
  private async answerFor(cacheKey: string, ask: () => Promise<CliResponse>): Promise<CliResponse> {
    const cached = this.solutionCache.get(cacheKey);
    if (cached) {
      console.log('[dedup] this exact picture was already answered and the answer already ran — the challenge is cycling, not still; re-solving it as animated.');
      this.repeatedAnswerSeen = true;
      return { actions: cached.actions, token_usage: [] };
    }
    const fresh = await ask();
    this.solutionCache.set(cacheKey, fresh);
    return fresh;
  }

  /**
   * Drop the ANSWER, keep the camera.
   *
   * The two are deliberately separable. An answer the widget refused is spent — reusing it just presses the
   * same wrong thing until the no-progress fence trips, three rounds in, with half the budget unspent. The
   * film is the opposite: every second it keeps running makes the next slice a better question. So a
   * refusal discards this and leaves `animatedFilm` alone; only a board that is GONE stops the camera.
   */
  private discardAnimatedPlan(): void {
    const dir = this.animatedPlan?.burstDir;
    this.animatedPlan = null;
    if (dir && fs.existsSync(dir)) rmdir(dir);
  }

  /** The board is gone: its film can never describe the next one, so end it and free the frames. */
  private async stopAnimatedFilm(): Promise<void> {
    const film = this.animatedFilm;
    this.animatedFilm = null;
    // The plan's slice goes too. This runs from the solve's `finally`, so on a thrown solve it is the only
    // thing that will, and a slice directory left behind is frames on what is a tmpfs on plenty of boxes.
    this.discardAnimatedPlan();
    if (film) await film.abandon();
  }

  /** Static or animated, decided by filming once. The film stays running for the branch that uses it. */
  private async classifyByRecording(el: ElementHandle): Promise<SettleVerdict> {
    // Only on an untouched board: after a click the film cannot tell the board's own motion from our feedback.
    if (this.actedOnBoard) return SettleVerdict.SETTLED;
    await this.releasePendingBurst();
    const rec = this.startKeyframeBurst(el);
    const kind = await this.ph(Phase.SETTLE, () => rec.ready());
    this.pendingBurst = rec;
    return kind;
  }

  private async releasePendingBurst(): Promise<void> {
    const rec = this.pendingBurst;
    this.pendingBurst = null;
    if (rec) await rec.abandon();
  }

  private shouldSpeculate(puzzleSource: Vendor, textMode: boolean): boolean {
    if (this.config.videoSolveEnabled === false) return false;
    if (this.config.speculativeBurstEnabled === false) return false;
    if (this.actedOnBoard) return false;
    // reCAPTCHA's dynamic 3x3 replaces tiles in place: a burst there films a fade and calls it a cycle.
    if (puzzleSource === Vendor.RECAPTCHA) return false;
    if (textMode) return false;
    return true;
  }

  /** One second look per solve: a repeated answer arms it, and the recording it takes spends it. */
  private shouldRetryAsAnimated(puzzleSource: Vendor): boolean {
    return this.repeatedAnswerSeen && !this.animatedProbeDone
      && puzzleSource !== Vendor.RECAPTCHA && this.config.videoSolveEnabled !== false;
  }

  private async getSolution(imagePath: string, puzzleSource: Vendor = Vendor.UNKNOWN, retryMode: RetryMode | null = null, textMode = false): Promise<CliResponse> {
    let cacheKey: string | null = null;
    try {
      cacheKey = `${sha1(imagePath)}|${puzzleSource}|${retryMode ?? ''}|${textMode ? 'text' : ''}`;
    } catch {
      cacheKey = null;
    }
    const ask = () => this.askModel(imagePath, puzzleSource, retryMode, textMode);
    return cacheKey ? this.answerFor(cacheKey, ask) : ask();
  }

  /** One inference through the CLI. */
  private async askModel(imagePath: string, puzzleSource: Vendor = Vendor.UNKNOWN, retryMode: RetryMode | null = null, textMode = false): Promise<CliResponse> {
    const { cliRoot, py } = this.resolveCli();
    const { apiKey = process.env.CAPTCHA_KRAKEN_API_KEY ?? process.env.VLLM_API_KEY } = this.config;
    const args = buildSolveArgs({ imagePath, model: this.modelName(cliRoot), puzzleSource, retryMode, textMode, expert: this.config.expert });
    console.log(`Executing CaptchaKraken CLI: ${redactCommand([py, ...args].join(' '), apiKey)}`);
    try {
      const { stdout, stderr } = await execFileAsync(py, args, { cwd: cliRoot, env: this.solveEnvironment(cliRoot, apiKey), maxBuffer: 10 * 1024 * 1024 });
      console.log('CaptchaKraken CLI stdout:', stdout);
      if (stderr) console.error('CaptchaKraken CLI stderr:', stderr);
      if (!stdout.trim()) throw new Error(`CLI returned empty output. Stderr: ${stderr}`);
      let actions: SolverResult = [];
      let tokenUsage: TokenUsage[] = [];
      for (const line of stdout.trim().split('\n')) {
        try {
          const parsed = JSON.parse(line);
          if (parsed.actions !== undefined && parsed.token_usage !== undefined) {
            actions = parsed.actions;
            tokenUsage = parsed.token_usage;
            break;
          }
          if (Array.isArray(parsed)) actions = parsed;
          else if (parsed.action && (parsed.target_bounding_box || parsed.target_coordinates || parsed.action === ActionKind.WAIT)) actions = [parsed];
        } catch { /* not json or not relevant */ }
      }
      return { actions, token_usage: tokenUsage };
    } catch (error: any) {
      throw this.cliError(error, 'Cannot solve this kind of captcha', 'Failed to execute captcha solver CLI');
    }
  }

  /** Re-solve when the frame changed during inference; twice means the board cycles. */
  private async solveFrameFreshnessGuarded(captchaElement: ElementHandle, initialShot: string,
                                           runQuery: (imagePath: string) => Promise<CliResponse>,
                                           opts: { recordingInFlight?: boolean } = {}): Promise<CliResponse> {
    const enabled = this.config.staleFrameReSolveEnabled !== false;
    const threshold = this.config.staleFrameDiffThreshold ?? 0.02;
    const maxReSolves = this.config.maxStaleFrameReSolves ?? 2;
    const ownedFrames: string[] = [];
    const mergedUsage: TokenUsage[] = [];
    // The movement question is asked against a live anchor: the model's shot was taken with animations frozen.
    const liveAnchor = tmp('ck_move');
    try {
      let currentPath = initialShot;
      let haveAnchor = false;
      try {
        await this.shot(captchaElement, liveAnchor, 2500, 'allow');
        haveAnchor = true;
      } catch { /* no anchor, no movement check */ }

      let response = await runQuery(currentPath);
      mergedUsage.push(...response.token_usage);
      if (!enabled) return response;

      if (!this.actedOnBoard && !this.repeatedAnswerSeen && haveAnchor
          && await this.captchaFrameChangedSince(captchaElement, liveAnchor, MOVED_DURING_INFERENCE_DIFF, 'allow')) {
        this.repeatedAnswerSeen = true;
        console.log('[freshness] the widget moved while the model was reading it, with nothing clicked — recording it rather than answering another still.');
      }
      // While a speculative burst is filming, the film is the better answer to "the screen changed".
      if (opts.recordingInFlight) return { actions: response.actions, token_usage: mergedUsage };

      let changedDuringInference = 0;
      for (let i = 0; i < maxReSolves; i++) {
        if (!(await this.captchaFrameChangedSince(captchaElement, currentPath, threshold))) break;
        if (++changedDuringInference >= 2 && !this.actedOnBoard) {
          this.repeatedAnswerSeen = true;
          console.log('[freshness] the frame changed twice during inference with nothing clicked — this board cycles; recording it rather than re-solving a screen that has gone.');
          return { actions: response.actions, token_usage: mergedUsage };
        }
        await this.waitForBoardPainted(captchaElement);
        const fresh = tmp(`freshsolve_${Date.now()}`);
        try {
          await this.shot(captchaElement, fresh);
        } catch {
          break;
        }
        ownedFrames.push(fresh);
        console.log(`[freshness] captcha frame changed during inference (re-solve ${i + 1}/${maxReSolves}); re-querying on the developed frame.`);
        currentPath = fresh;
        response = await runQuery(currentPath);
        mergedUsage.push(...response.token_usage);
      }
      return { actions: response.actions, token_usage: mergedUsage };
    } finally {
      unlink(liveAnchor);
      for (const f of ownedFrames) unlink(f);
    }
  }

  private async captchaFrameChangedSince(captchaElement: ElementHandle, priorPath: string, threshold: number, _animations: 'allow' | 'disabled' = 'disabled'): Promise<boolean> {
    const probe = tmp('freshcheck');
    try {
      await this.shot(captchaElement, probe);
      const res = await this.runCvTool('check-movement', { a: priorPath, b: probe, threshold }, ['check-movement', priorPath, probe, String(threshold)]);
      return !!(res && res.has_movement);
    } catch {
      return false;
    } finally {
      unlink(probe);
    }
  }

  private async elementFrameHash(el: ElementHandle): Promise<string | null> {
    const f = tmp('fh');
    try {
      await this.shot(el, f);
      return sha1(f);
    } catch {
      return null;
    } finally {
      unlink(f);
    }
  }

  /** Poll until the pixels stop changing. `motionStreak` exits early: rotating_obj_video changes every 133-171ms and once spent 4.5s proving it. */
  private async waitForElementSettled(el: ElementHandle, opts?: { pollMs?: number; settleFrames?: number; maxMs?: number; animatedAfterMs?: number; motionStreak?: number; threshold?: number }): Promise<SettleVerdict> {
    const cfg = this.config;
    const pollMs = opts?.pollMs ?? cfg.settlePollMs ?? 220;
    const settleFrames = opts?.settleFrames ?? cfg.settleFrames ?? 2;
    const maxMs = opts?.maxMs ?? cfg.settleTimeoutMs ?? 9000;
    const animatedAfterMs = opts?.animatedAfterMs ?? cfg.animatedChallengeAfterMs ?? 4500;
    const motionStreak = opts?.motionStreak ?? cfg.animatedMotionStreak ?? 5;
    const threshold = opts?.threshold ?? cfg.settleDiffThreshold ?? 0.01;
    const start = Date.now();
    let prev: string | null = null;
    let stillStreak = 0;
    let movedStreak = 0;
    const frames: string[] = [];
    try {
      while (Date.now() - start < maxMs) {
        const f = tmp('settle');
        try { await this.shot(el, f); } catch { await delay(pollMs); continue; }
        frames.push(f);
        if (prev) {
          const res = await this.runCvTool('check-movement', { a: prev, b: f, threshold }, ['check-movement', prev, f, String(threshold)]);
          const moved = !!(res && res.has_movement);
          stillStreak = moved ? 0 : stillStreak + 1;
          movedStreak = moved ? movedStreak + 1 : 0;
          unlink(frames.shift());
          if (stillStreak >= settleFrames) return SettleVerdict.SETTLED;
          if (moved && (Date.now() - start) >= animatedAfterMs) return SettleVerdict.ANIMATED;
          if (motionStreak && movedStreak >= motionStreak) return SettleVerdict.ANIMATED;
        }
        prev = frames[frames.length - 1];
        await delay(pollMs);
      }
      return SettleVerdict.TIMEOUT;
    } finally {
      for (const f of frames) unlink(f);
    }
  }

  private async waitForChangeSince(el: ElementHandle, sinceHash: string, opts?: { pollMs?: number; maxMs?: number }): Promise<boolean> {
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

  /** Drift the cursor over the widget while `fn` (the model call) runs; cancelled the instant it resolves. */
  private async withIdleWander<T>(page: Page, element: ElementHandle, fn: () => Promise<T>): Promise<T> {
    if (this.config.idleMouseWander === false || !this.human.hovers) return fn();
    let box: Box | null = null;
    try { box = await element.boundingBox(); } catch { box = null; }
    if (!box || box.width < 20 || box.height < 20) return fn();
    const b = box;
    let stop = false;
    const waker: { fn: (() => void) | null } = { fn: null };
    const nap = (ms: number) => new Promise<void>((resolve) => {
      const timer = setTimeout(() => { waker.fn = null; resolve(); }, ms);
      waker.fn = () => { clearTimeout(timer); waker.fn = null; resolve(); };
    });
    const pad = 0.18;
    const wander = (async () => {
      await nap(120 + Math.random() * 180);
      while (!stop) {
        try {
          await this.performSmoothMove(page, b.x + b.width * (pad + Math.random() * (1 - 2 * pad)), b.y + b.height * (pad + Math.random() * (1 - 2 * pad)));
        } catch {
          break;
        }
        if (stop) break;
        await nap(180 + Math.random() * 360);
      }
    })();
    try {
      return await fn();
    } finally {
      stop = true;
      waker.fn?.();
      await wander.catch(() => {});
    }
  }

  async move(page: Page, selectorOrElement: string | ElementHandle, options: { paddingPercentage?: number } = {}): Promise<void> {
    const elem = typeof selectorOrElement === 'string'
      ? await page.waitForSelector(selectorOrElement, { state: 'visible', timeout: 10000 })
      : selectorOrElement;
    if (!elem) throw new Error(`Element not found: ${selectorOrElement}`);
    // Bounded: Playwright's default 30s stability wait hangs on an animating frame.
    try { await elem.scrollIntoViewIfNeeded({ timeout: 2000 }); } catch { /* still where boundingBox says it is */ }
    const box = await elem.boundingBox();
    if (!box) throw new Error(`Element has no bounding box: ${selectorOrElement}`);
    const padding = (options.paddingPercentage || 25) / 100;
    await this.performSmoothMove(page,
      box.x + box.width * (padding + Math.random() * (1 - 2 * padding)),
      box.y + box.height * (padding + Math.random() * (1 - 2 * padding)));
  }

  async moveAndClick(page: Page, element: ElementHandle) {
    await this.move(page, element);
    await this.ph(Phase.MOUSE, () => this.human.click(page, this.human.at));
  }

  private async performSmoothMove(page: Page, x: number, y: number) {
    await this.ph(Phase.MOUSE, () => this.human.move(page, [x, y]));
  }

  /** Element-relative click point: a random spot inside the box, inset 10% off its border. */
  private clickPointFor(action: ClickAction, elementBox: Box): [number, number] | null {
    if (action.target_bounding_box) {
      const [minX, minY, maxX, maxY] = action.target_bounding_box;
      const w = (maxX - minX) * elementBox.width;
      const h = (maxY - minY) * elementBox.height;
      return [minX * elementBox.width + w * 0.1 + Math.random() * w * 0.8, minY * elementBox.height + h * 0.1 + Math.random() * h * 0.8];
    }
    if (action.target_coordinates) return [action.target_coordinates[0] * elementBox.width, action.target_coordinates[1] * elementBox.height];
    return null;
  }

  /** Park on the target first, so only a mouse-down separates the right screen from the click. */
  private async clickWhenFrameMatches(page: Page, element: ElementHandle, action: ClickAction, elementBox: Box, awaitKeyframe: string): Promise<void> {
    const rel = this.clickPointFor(action, elementBox);
    if (!rel) {
      console.warn('Click action received without coordinates or bounding box', action);
      return;
    }
    const at: [number, number] = [elementBox.x + rel[0], elementBox.y + rel[1]];
    await this.ph(Phase.MOUSE, () => this.human.move(page, at));
    await this.waitForKeyframe(element, awaitKeyframe, rel[0] / elementBox.width, rel[1] / elementBox.height);
    this.actedOnBoard = true;
    await this.ph(Phase.MOUSE, () => this.human.click(page, at));
  }

  private async executeClick(page: Page, _element: ElementHandle, action: ClickAction, elementBox: Box) {
    this.actedOnBoard = true;
    const rel = this.clickPointFor(action, elementBox);
    if (!rel) {
      console.warn('Click action received without coordinates or bounding box', action);
      return;
    }
    await this.ph(Phase.MOUSE, () => this.human.click(page, [elementBox.x + rel[0], elementBox.y + rel[1]]));
  }

  private async executeDrag(page: Page, _element: ElementHandle,
                            action: { source_bounding_box: [number, number, number, number]; target_bounding_box: [number, number, number, number] },
                            elementBox: Box) {
    this.actedOnBoard = true;
    const center = (bbox: [number, number, number, number]): [number, number] =>
      [elementBox.x + ((bbox[0] + bbox[2]) / 2) * elementBox.width, elementBox.y + ((bbox[1] + bbox[3]) / 2) * elementBox.height];
    await this.ph(Phase.MOUSE, () => this.human.drag(page, center(action.source_bounding_box), center(action.target_bounding_box)));
  }

  /**
   * The slider piece's box: the first visible match small enough to be a piece. Runs during detection, so it must
   * not set `actedOnBoard`: marking there disabled `shouldSpeculate` on every slide solve.
   */
  private async measurePieceBox(scope: Scope, widgetWidth: number): Promise<Box | null> {
    const boxes = await Promise.all((await handles(await visible(scope, PIECE_SELECTORS))).map((h) => h.boundingBox().catch(() => null)));
    return boxes.find((b): b is Box => !!b && b.width >= MIN_PIECE_PX && b.width <= widgetWidth * MAX_PIECE_FRACTION) ?? null;
  }

  private async findControl(scope: Scope, selectors: readonly string[]): Promise<ElementHandle | null> {
    const [first] = await visible(scope, selectors);
    return first ? handleOf(first) : null;
  }

  /**
   * The text box inside the widget, else a vendor-named one in its enclosing fieldset/form, never the page: the generic
   * tail outside the widget is how a captcha's answer lands in a login form's username box. The widening exists for
   * BotDetect, whose 280x50 `.BDC_CaptchaDiv` holds only the image while `#captchaCode` sits in a sibling div.
   */
  private async answerBox(scope: Scope, at?: Locator | null): Promise<ElementHandle | null> {
    const inside = await this.findControl(scope, TEXT_INPUT_SELECTORS);
    if (inside !== null || !at) return inside;
    const around = await Promise.all(['ancestor::fieldset[1]', 'ancestor::form[1]'].map((axis) => this.findControl(at.locator(`xpath=${axis}`), TEXT_INPUT_VENDOR_SELECTORS)));
    return around.find((h) => h !== null) ?? null;
  }

  private async executeType(page: Page, scope: Scope, action: TypeAction, at?: Locator | null): Promise<boolean> {
    this.actedOnBoard = true;
    const text = action.text ?? '';
    const field = text ? await this.answerBox(scope, at) : null;
    if (!field) {
      console.warn('Type action, but no text box in the widget; skipping.');
      return false;
    }
    await this.moveAndClick(page, field);
    if (!(await this.human.typeText(page, field, text))) return false;
    console.log(`Typed ${text.length} character(s) into the captcha field.`);
    return true;
  }

  private async trackPiece(element: ElementHandle, beforePath: string, afterPath: string, exclude: [number, number, number, number], travel = 0): Promise<TrackedPiece | null> {
    try {
      await this.shot(element, afterPath, this.config.elementScreenshotTimeoutMs ?? 8000);
      const res = await this.runCvTool('track-piece', { before: beforePath, after: afterPath, exclude, travel },
        ['track-piece', beforePath, afterPath, JSON.stringify(exclude), String(travel)]);
      return res && res.bbox ? { bbox: res.bbox, piece: res.piece ?? null } : null;
    } catch (e) {
      console.warn('track-piece failed:', e);
      return null;
    }
  }

  private shotScale(shot: string, cssWidth: number): number {
    const dims = readPngDimensions(shot);
    return dims && cssWidth > 0 ? dims.width / cssWidth : 1;
  }

  /** Aim the handle at the slot once, then look and correct until the piece is home. Release is the submit. */
  private async executeSlide(page: Page, element: ElementHandle, scope: Scope, action: DragAction, elementBox: Box): Promise<boolean> {
    this.actedOnBoard = true;
    const tb = action.target_bounding_box;
    const targetX = ((tb[0] + tb[2]) / 2) * elementBox.width;

    const handle = await this.findControl(scope, SLIDER_HANDLE_SELECTORS);
    if (!handle) {
      const piece = await this.findControl(scope, PIECE_SELECTORS);
      const box = piece ? await piece.boundingBox() : null;
      if (!box) {
        console.warn('Slide action, but the widget has neither a slider nor a draggable piece.');
        return false;
      }
      const targetY = ((tb[1] + tb[3]) / 2) * elementBox.height;
      console.log('No slider track; dragging the piece to the slot directly.');
      await this.human.drag(page, [box.x + box.width / 2, box.y + box.height / 2], [elementBox.x + targetX, elementBox.y + targetY]);
      return true;
    }

    const hbox = await handle.boundingBox();
    if (!hbox) return false;
    const startX = hbox.x + hbox.width / 2;
    const holdY = hbox.y + hbox.height / 2;
    // Mask from the handle's band to the widget's bottom, not the handle's bottom: the filled track moves too, and
    // masked to the band alone the loop once derived a 135.4px piece on Tencent's track against 42.0px masked to the bottom.
    const pad = Math.max(4, hbox.height * 0.35);
    const band: [number, number, number, number] = [0, hbox.y - elementBox.y - pad, elementBox.width, elementBox.height];
    const shots = [tmp('slide_0'), tmp('slide_1')];
    try {
      await this.move(page, handle, { paddingPercentage: 30 });
      await this.human.press(page);
      await this.human.pause(PauseKind.GRAB);
      await this.shot(element, shots[0], this.config.elementScreenshotTimeoutMs ?? 8000);
      const scale = this.shotScale(shots[0], elementBox.width);
      const exclude = band.map((v) => v * scale) as [number, number, number, number];
      const liveCentre = async (): Promise<number | null> => {
        const b = await this.measurePieceBox(scope, elementBox.width);
        return b ? b.x + b.width / 2 - elementBox.x : null;
      };

      const restCentre = (await liveCentre()) ?? (startX - elementBox.x);
      let offset = targetX - restCentre;
      await this.performSmoothMove(page, startX + offset, holdY);

      const widths: Array<[number, number]> = [];
      let lastBox: [number, number, number, number] | null = null;
      let lastPiece: { centre: number, width: number } | null = null;
      let lastCentre: number | null = null;
      let ratio = 1;
      let i = 0;
      for (; i < SLIDE_MAX_CORRECTIONS; i++) {
        await this.human.pause(PauseKind.PROBE);
        const seen = await this.trackPiece(element, shots[0], shots[1], exclude, offset * ratio * scale);
        if (seen) {
          widths.push([offset, (seen.bbox[2] - seen.bbox[0]) / scale]);
          lastBox = seen.bbox;
          lastPiece = seen.piece;
        }
        const solved = solveSlideGeometry(widths, elementBox.width);
        ratio = solved.ratio;
        const live = await liveCentre();
        const pieceCentre = live !== null ? live
          : lastPiece !== null ? lastPiece.centre / scale
            : (lastBox && solved.pieceWidth !== null ? lastBox[2] / scale - solved.pieceWidth / 2 : null);
        if (pieceCentre === null) continue;
        lastCentre = pieceCentre;
        const error = targetX - pieceCentre;
        console.log(`[slide] ${live !== null ? 'the piece element' : lastPiece !== null ? 'the pixel diff' : "the diff's union"} puts it at ${Math.round(pieceCentre)}px, want ${Math.round(targetX)}px, ratio ${ratio.toFixed(2)}`);
        if (Math.abs(error) <= SLIDE_TOLERANCE_PX) break;
        offset += error / ratio;
        await this.performSmoothMove(page, startX + offset, holdY);
      }
      if (lastCentre === null) {
        console.warn('Slider: the piece never resolved on screen; released where the opening sweep put it.');
      } else if (i >= SLIDE_MAX_CORRECTIONS) {
        const settled = await liveCentre();
        const left = targetX - (settled !== null ? settled : lastCentre);
        if (Math.abs(left) > SLIDE_TOLERANCE_PX) console.warn(`[slide] out of corrections with ${left >= 0 ? '+' : ''}${left.toFixed(1)}px still to go — releasing off-target`);
      }
      await this.human.pause(PauseKind.SETTLE);
    } finally {
      try { await this.human.release(page); } catch { /* the page may have navigated */ }
      for (const s of shots) unlink(s);
    }
    return true;
  }
}
