/**
 * Puppeteer → CaptchaKraken adapter.
 *
 * The solver speaks the Playwright API surface (see playwright-types.ts). Puppeteer
 * is ~95% the same shape but differs in a handful of method names/options. Rather
 * than couple the solver to either library, this thin adapter wraps a Puppeteer
 * `Page` (and, lazily, the `Frame`/`ElementHandle` objects it hands back) so it
 * satisfies the structural `PlaywrightPage` interface the solver consumes.
 *
 * The deltas it bridges (verified against Puppeteer 24.x):
 *   - `page.viewportSize()`            → `page.viewport()`  (same `{width,height}` shape)
 *   - `page.waitForTimeout(ms)`        → removed in modern Puppeteer; use a timer
 *   - `*.waitForSelector(sel, {state}) → Puppeteer uses `{visible}/{hidden}`
 *   - `handle.getAttribute(name)`      → `handle.evaluate((el,n)=>el.getAttribute(n), name)`
 *   - `handle.textContent()`           → `handle.evaluate(el=>el.textContent)`
 *   - `handle.scrollIntoViewIfNeeded()`→ `handle.scrollIntoView()`
 * Everything else (`$`, `$$`, `$eval`, `waitForFunction`, `mouse.*`,
 * `screenshot({path})`, `boundingBox`, `contentFrame`, `isVisible`) is
 * call-compatible and passed straight through.
 *
 * Usage:
 * ```typescript
 * import puppeteer from 'puppeteer';
 * import { CaptchaKrakenSolver, fromPuppeteer } from 'captchakraken';
 *
 * const browser = await puppeteer.launch({ headless: false });
 * const page = await browser.newPage();
 * await page.goto('https://www.google.com/recaptcha/api2/demo');
 *
 * const solver = new CaptchaKrakenSolver();
 * await solver.solve(fromPuppeteer(page));
 * await browser.close();
 * ```
 */

import {
  PlaywrightPage,
  PlaywrightFrame,
  PlaywrightElementHandle,
  BoundingBoxRect,
  ViewportSize,
} from './playwright-types';

/**
 * Minimal structural view of the Puppeteer objects we touch. We deliberately
 * type these loosely (no `puppeteer` import) so the package keeps ZERO browser
 * dependencies — the caller supplies a real Puppeteer `Page` at runtime.
 */
interface PuppeteerSelectorState {
  state?: 'attached' | 'detached' | 'visible' | 'hidden';
  timeout?: number;
}
interface PuppeteerElementHandle {
  screenshot(options?: { path?: string }): Promise<unknown>;
  contentFrame(): Promise<PuppeteerFrame | null>;
  boundingBox(): Promise<BoundingBoxRect | null>;
  scrollIntoView(): Promise<void>;
  isVisible(): Promise<boolean>;
  evaluate(pageFunction: (el: any, ...args: any[]) => any, ...args: any[]): Promise<any>;
}
interface PuppeteerFrame {
  $(selector: string): Promise<PuppeteerElementHandle | null>;
  waitForSelector(selector: string, options?: any): Promise<PuppeteerElementHandle | null>;
  waitForFunction(pageFunction: Function | string, options?: any, ...args: any[]): Promise<unknown>;
}
interface PuppeteerPage {
  mouse: {
    move(x: number, y: number, options?: { steps?: number }): Promise<void>;
    down(options?: any): Promise<void>;
    up(options?: any): Promise<void>;
  };
  waitForSelector(selector: string, options?: any): Promise<PuppeteerElementHandle | null>;
  viewport(): ViewportSize | null;
  $(selector: string): Promise<PuppeteerElementHandle | null>;
  $$(selector: string): Promise<PuppeteerElementHandle[]>;
  $eval(selector: string, pageFunction: (element: Element) => any, ...args: any[]): Promise<any>;
}

/** Translate Playwright `{state}` selector options to Puppeteer `{visible}/{hidden}`. */
function toPuppeteerSelectorOptions(options?: PuppeteerSelectorState): any {
  if (!options) return undefined;
  const { state, timeout } = options;
  const out: any = {};
  if (timeout !== undefined) out.timeout = timeout;
  if (state === 'visible') out.visible = true;
  else if (state === 'hidden') out.hidden = true;
  // 'attached'/'detached' have no direct Puppeteer flag — default wait (attached)
  // is the closest match, so we pass no visibility flag.
  return out;
}

function wrapHandle(h: PuppeteerElementHandle | null): PlaywrightElementHandle | null {
  if (!h) return null;
  return {
    screenshot: (options) => h.screenshot(options) as Promise<Buffer>,
    contentFrame: async () => wrapFrame(await h.contentFrame()),
    boundingBox: () => h.boundingBox(),
    scrollIntoViewIfNeeded: () => h.scrollIntoView(),
    getAttribute: (name) => h.evaluate((el: Element, n: string) => el.getAttribute(n), name),
    isVisible: () => h.isVisible(),
    textContent: () => h.evaluate((el: Element) => el.textContent),
  };
}

function wrapFrame(f: PuppeteerFrame | null): PlaywrightFrame | null {
  if (!f) return null;
  return {
    $: async (selector) => wrapHandle(await f.$(selector)),
    waitForSelector: async (selector, options) =>
      wrapHandle(await f.waitForSelector(selector, toPuppeteerSelectorOptions(options))),
    waitForFunction: (pageFunction, arg, options) =>
      // Playwright: (fn, arg, {timeout,polling}); Puppeteer: (fn, {timeout,polling}, ...args).
      f.waitForFunction(pageFunction as any, options, arg),
  };
}

/**
 * Wrap a Puppeteer `Page` so it satisfies the solver's structural Playwright
 * `Page`. Pass the result to `solver.solve(...)`.
 */
export function fromPuppeteer(page: PuppeteerPage): PlaywrightPage {
  return {
    mouse: {
      move: (x, y, options) => page.mouse.move(x, y, options),
      down: (options) => page.mouse.down(options),
      up: (options) => page.mouse.up(options),
    },
    waitForTimeout: (timeout) => new Promise<void>((resolve) => setTimeout(resolve, timeout)),
    waitForSelector: async (selector, options) =>
      wrapHandle(await page.waitForSelector(selector, toPuppeteerSelectorOptions(options))),
    viewportSize: () => page.viewport(),
    $: async (selector) => wrapHandle(await page.$(selector)),
    $$: async (selector) => (await page.$$(selector)).map((h) => wrapHandle(h)!).filter(Boolean),
    $eval: (selector, pageFunction, arg) => page.$eval(selector, pageFunction as any, arg),
  };
}
