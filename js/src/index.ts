export * from './types';
export { CaptchaKrakenSolver } from './solver';

/**
 * Errors raised when the **hosted** CaptchaKraken API refuses a solve — out of
 * credits, rate limited, key rejected, attempt abandoned.
 *
 * `message` is already a complete sentence naming the product and, where one
 * exists, the URL that fixes it, so printing it is enough. `code` is the stable
 * contract to branch on; the prose is not.
 *
 * @example
 * ```typescript
 * import { CaptchaKrakenSolver, CaptchaKrakenAPIError } from 'captchakraken';
 *
 * try {
 *   await solver.solve(page);
 * } catch (e) {
 *   if (e instanceof CaptchaKrakenAPIError && e.code === 'insufficient_credits') {
 *     console.error(`Top up: ${e.resolutionUrl}`);
 *   } else throw e;
 * }
 * ```
 */
export { CaptchaKrakenAPIError } from './errors';
export type { CaptchaKrakenErrorCode } from './errors';

/**
 * Adapter for driving the solver with **Puppeteer** instead of a Playwright
 * launcher. Puppeteer's `Page` is API-compatible with Playwright's except for a
 * few method names/options; `fromPuppeteer(page)` wraps it so `solve()` accepts
 * it. Playwright-family launchers (vanilla `playwright`, `patchright`,
 * `camoufox-js`) need no adapter — pass their `Page` directly.
 *
 * @example
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
export { fromPuppeteer } from './puppeteer-adapter';

/**
 * The Playwright `Page` type the solver operates on.
 *
 * This is an implementation-neutral, structurally-typed Playwright `Page` — a
 * duck-typed surface defined by this package, NOT imported from any browser
 * library. The package depends on no concrete browser implementation, so you
 * can drive the solver with ANY Playwright-compatible launcher: vanilla
 * `playwright`, `patchright`, `camoufox-js`, or anything else that hands back a
 * standard Playwright `Page`. They all structurally satisfy this type — pass
 * yours directly, no cast needed.
 *
 * @example
 * ```typescript
 * // Pick whichever Playwright-compatible launcher you want — install it
 * // yourself; this package does not bundle a browser:
 * import { chromium } from 'playwright';          // vanilla
 * // import { chromium } from 'patchright';       // stealth-patched
 * // import { Camoufox } from 'camoufox-js';      // Firefox stealth
 * import { CaptchaKrakenSolver } from 'captchakraken';
 *
 * const browser = await chromium.launch({ headless: false });
 * const page = await (await browser.newContext()).newPage();
 * await page.goto('https://www.google.com/recaptcha/api2/demo');
 *
 * // Reads VLLM_BASE_URL + CAPTCHA_KRAKEN_API_KEY from the environment.
 * const solver = new CaptchaKrakenSolver();
 * await solver.solve(page);   // detect → solve grid → click → verify
 *
 * await browser.close();
 * ```
 */
export type { Page, PlaywrightPage, PlaywrightFrame, PlaywrightElementHandle } from './playwright-types';

/**
 * Auto-solve watcher: `solver.watch(page)` installs a background poller that
 * solves captchas as they appear, and returns a handle with `stop()`.
 *
 * Injects nothing into the page, on any platform — see watcher.ts. On Camoufox
 * its DOM reads run in the isolated Juggler world by default.
 *
 * @example
 * ```typescript
 * const solver = new CaptchaKrakenSolver();
 * const watcher = solver.watch(page, { onSolved: r => console.log(r.isSolved) });
 * // ... your automation ...
 * await watcher.stop();
 * ```
 */
export { watchPage } from './watcher';
export type { CaptchaWatcher, WatchOptions, WatchableSolver } from './watcher';
