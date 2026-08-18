/**
 * Auto-solve watcher — install once, and captchas are solved as they appear.
 *
 * WHY THIS IS A POLL AND NOT AN INJECTED OBSERVER
 * The obvious implementation is a `MutationObserver` inside the page that calls
 * out through `exposeBinding`. That is faster to react, and it is also the one
 * design that cannot be made stealthy on every platform: `exposeBinding` puts a
 * function on `window` and an observer is script the page can enumerate. On
 * Camoufox both are sandboxed and invisible; on vanilla Playwright or Puppeteer
 * they are not, and a captcha vendor is exactly the party that looks.
 *
 * So the watcher injects NOTHING. It drives the same `detectCaptcha()` the
 * solver already uses, from the driver side, on a timer. Consequences worth
 * knowing:
 *
 *   - Nothing is added to the page on ANY platform, so there is no new
 *     detection surface to reason about per launcher.
 *   - On Camoufox the probe's DOM reads run in Camoufox's isolated Juggler
 *     world for free — that is its default for all Playwright evaluation
 *     (`main_world_eval` / an `mw:` prefix is the opt-OUT). The watcher never
 *     opts out, so it is isolated there by construction, not by special-casing.
 *   - Reaction time is bounded by `intervalMs`, not by the mutation itself.
 *     A captcha that appears just after a tick waits up to one interval.
 *
 * DETECTION IS THE SOLVER'S, NOT A COPY
 * The trigger is `solver.detectCaptcha(page)`, the same method `solve()` calls,
 * so "what counts as a captcha" has exactly one definition. An earlier sketch
 * kept a union of the vendor selectors here for a cheaper single-roundtrip
 * probe; it drifted from VENDOR_WIDGET_LOCATORS the first time a vendor was
 * added, which is the failure this indirection exists to prevent.
 *
 * ```typescript
 * const solver = new CaptchaKrakenSolver();
 * const watcher = solver.watch(page, { onSolved: r => console.log(r.isSolved) });
 * // ... your automation runs; captchas are solved underneath it ...
 * await watcher.stop();
 * ```
 */

import { PlaywrightPage as Page } from './playwright-types';
import { SolveResult } from './types';

/**
 * The slice of the solver the watcher drives.
 *
 * Structural rather than an import of `CaptchaKrakenSolver` so this module has
 * no runtime dependency on solver.ts (which imports this one for the `watch()`
 * method — a real cycle under CommonJS), and so tests can drive it with a fake.
 */
export interface WatchableSolver {
  detectCaptcha(page: Page): Promise<unknown | null>;
  solve(page: Page): Promise<SolveResult | void>;
}

export interface WatchOptions {
  /**
   * Milliseconds between detection probes. Default 1000.
   *
   * This is the latency floor for noticing a captcha, and each tick costs one
   * `detectCaptcha()` — a handful of selector queries. Below ~250ms you are
   * paying real CPU for latency the solve itself (seconds) makes irrelevant.
   */
  intervalMs?: number;
  /** Stop automatically after this many successful solves. Default: unlimited. */
  maxSolves?: number;
  /**
   * Wait this long after a solve THROWS before probing again. Default 5000.
   *
   * Without it a permanently unsupported challenge — an invisible reCAPTCHA v3,
   * say — turns the watcher into a hot loop that re-attempts and re-bills every
   * `intervalMs` forever.
   */
  errorBackoffMs?: number;
  /** Called after each solve that returns a result. Exceptions here are swallowed. */
  onSolved?: (result: SolveResult) => void | Promise<void>;
  /**
   * Called when detection or solving throws. Exceptions here are swallowed.
   *
   * Errors are reported, never fatal: `NoCaptchaFoundError` fires routinely
   * when a widget disappears between the probe and the solve, and a watcher
   * that died on it would be useless.
   */
  onError?: (error: unknown) => void | Promise<void>;
}

export interface CaptchaWatcher {
  /** Stop probing and resolve once any in-flight solve has finished. */
  stop(): Promise<void>;
  /** False once stopped, the page closed, or `maxSolves` was reached. */
  readonly running: boolean;
  /** Successful solves so far. */
  readonly solves: number;
}

/** A page that has closed, where the launcher exposes that (Playwright and Puppeteer both do). */
function isPageClosed(page: Page): boolean {
  return typeof page.isClosed === 'function' ? page.isClosed() : false;
}

/** Report to a user callback without letting its failure reach the loop. */
async function report<T>(fn: ((value: T) => void | Promise<void>) | undefined, value: T): Promise<void> {
  if (!fn) return;
  try {
    await fn(value);
  } catch {
    // A throwing callback is the caller's bug and must not stop the watcher.
  }
}

export function watchPage(solver: WatchableSolver, page: Page, options: WatchOptions = {}): CaptchaWatcher {
  const intervalMs = options.intervalMs ?? 1000;
  const errorBackoffMs = options.errorBackoffMs ?? 5000;
  const maxSolves = options.maxSolves ?? Infinity;

  let running = true;
  let solves = 0;
  // Resolved by the loop on its way out, so stop() can await a solve in flight.
  // Definite assignment: the Promise executor runs synchronously, so `finished`
  // is set before any code below can reach it — TS cannot prove that itself.
  let finished!: () => void;
  const done = new Promise<void>((resolve) => { finished = resolve; });
  // Held so stop() can cut a pending sleep short instead of waiting it out.
  let wake: (() => void) | null = null;

  const sleep = (ms: number) => new Promise<void>((resolve) => {
    const timer = setTimeout(() => { wake = null; resolve(); }, ms);
    wake = () => { clearTimeout(timer); wake = null; resolve(); };
  });

  (async () => {
    try {
      while (running) {
        await sleep(intervalMs);
        if (!running) break;
        if (isPageClosed(page)) break;

        try {
          if (!(await solver.detectCaptcha(page))) continue;
          // Re-check: detectCaptcha can take a moment, and stop() during it
          // should not be followed by a fresh multi-second solve.
          if (!running) break;

          const result = await solver.solve(page);
          if (result) {
            solves += 1;
            await report(options.onSolved, result);
          }
          if (solves >= maxSolves) break;
        } catch (error) {
          if (isPageClosed(page)) break;
          await report(options.onError, error);
          await sleep(errorBackoffMs);
        }
      }
    } finally {
      running = false;
      finished();
    }
  })();

  return {
    stop(): Promise<void> {
      running = false;
      if (wake) wake();
      return done;
    },
    get running() { return running; },
    get solves() { return solves; },
  };
}
