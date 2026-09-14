import { PlaywrightPage as Page } from './playwright-types';
import { SolveResult } from './types';

export interface WatchableSolver {
  detectCaptcha(page: Page): Promise<unknown | null>;
  solve(page: Page): Promise<SolveResult | void>;
}

export interface WatchOptions {
  intervalMs?: number;

  maxSolves?: number;

  errorBackoffMs?: number;

  onSolved?: (result: SolveResult) => void | Promise<void>;

  onError?: (error: unknown) => void | Promise<void>;
}

export interface CaptchaWatcher {
  stop(): Promise<void>;

  readonly running: boolean;

  readonly solves: number;
}

function isPageClosed(page: Page): boolean {
  return typeof page.isClosed === 'function' ? page.isClosed() : false;
}

async function report<T>(fn: ((value: T) => void | Promise<void>) | undefined, value: T): Promise<void> {
  if (!fn) return;
  try {
    await fn(value);
  } catch {
  }
}

export function watchPage(solver: WatchableSolver, page: Page, options: WatchOptions = {}): CaptchaWatcher {
  const intervalMs = options.intervalMs ?? 1000;
  const errorBackoffMs = options.errorBackoffMs ?? 5000;
  const maxSolves = options.maxSolves ?? Infinity;

  let running = true;
  let solves = 0;

  let finished!: () => void;
  const done = new Promise<void>((resolve) => { finished = resolve; });

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
