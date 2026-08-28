export * from './types';
export { CaptchaKrakenSolver } from './solver';

/**
 * The served LoRA name this client will ask for when `model` is left unset —
 * `CAPTCHA_LORA_NAME`, else `models.json`'s `latest`, else the shipped pin.
 *
 * Exported so anything that REPORTS which adapter was driven can ask the client
 * instead of re-deriving the answer. That distinction is not cosmetic: the name
 * selects the prompt generation, so a reporter that guesses it can disagree with
 * the solver and describe a run that never happened.
 */
export { resolveLoraName } from './model-name';

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

/**
 * Humanisation: how the driver moves. `humanization: 'mouse' | 'mobile' |
 * 'none'` picks a built-in; `humanizer` takes one of your own.
 *
 * `MobileHumanizer` emits real touch events with finger kinematics, through a
 * `TouchBackend` — CDP against a Chromium-family page, or W3C pointer actions
 * against an Appium / WebdriverIO driver on a real handset.
 *
 * @example Mobile emulation in the browser
 * ```typescript
 * import { chromium, devices } from 'playwright';
 * import { CaptchaKrakenSolver } from 'captchakraken';
 *
 * const browser = await chromium.launch();
 * const context = await browser.newContext({ ...devices['Pixel 7'], hasTouch: true });
 * const page = await context.newPage();
 * await page.goto('https://example.com/signup');
 *
 * await new CaptchaKrakenSolver({ humanization: 'mobile' }).solve(page);
 * ```
 *
 * @example A real device over Appium
 * ```typescript
 * const solver = new CaptchaKrakenSolver({
 *   humanization: 'mobile',
 *   touchDriver: driver,                                  // the WebdriverIO browser
 *   touchTransform: { scale: 3, origin: [0, 132] },       // CSS px -> screen px
 * });
 * await solver.solve(page);
 * ```
 */
export {
  MouseHumanizer,
  MobileHumanizer,
  NullHumanizer,
  BaseHumanizer,
  resolveHumanizer,
  touchBackendFor,
  CdpTouchBackend,
  AppiumTouchBackend,
  TouchscreenTouchBackend,
  PAUSE_KINDS,
  MODES as HUMANIZATION_MODES,
} from './humanize';
export type {
  Humanizer,
  HumanizationMode,
  HumanizerOptions,
  TouchBackend,
  TouchSample,
  TouchTransform,
  PauseKind,
} from './humanize';
