/**
 * Minimal, implementation-neutral structural types for the slice of the
 * Playwright API the solver actually uses.
 *
 * **Why these exist.** CaptchaKraken never launches a browser — you bring your
 * own Playwright-compatible launcher and hand `solve()` a `Page`. To stay truly
 * browser-agnostic the package depends on NO concrete browser implementation
 * (not `playwright`, not `patchright`, not `camoufox-js`). Typing the public API
 * against any one of those packages' `Page` would (a) force that package into
 * consumers' trees and (b) break across version skew — e.g. `patchright` pins
 * its own forked, older core whose `Page` is missing the newest `playwright-core`
 * methods, so a `patchright` page won't structurally match a `playwright-core`
 * page and vice-versa.
 *
 * Instead we declare a duck-typed surface covering only what the solver calls.
 * Every real Playwright `Page` / `Frame` / `ElementHandle` — from vanilla
 * `playwright`, `patchright`, `camoufox-js`, or anything else — structurally
 * satisfies these, regardless of which Playwright version it was built against.
 *
 * Keep these in sync with `solver.ts`: if the solver starts calling a new
 * Playwright method, add it here. The set is intentionally small and stable —
 * these are long-standing Playwright primitives, not bleeding-edge additions.
 */

/** `{ x, y, width, height }` in CSS pixels — the shape `boundingBox()` returns. */
export interface BoundingBoxRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** `{ width, height }` — the shape `viewportSize()` returns. */
export interface ViewportSize {
  width: number;
  height: number;
}

/**
 * Structural subset of Playwright's `ElementHandle`. Returned by `Page.$` /
 * `Frame.$` / `Page.waitForSelector` and accepted by the solver as the captcha
 * element / verify button / iframe handle.
 */
export interface PlaywrightElementHandle {
  /** Screenshot just this element to a PNG file (the only option the solver passes). */
  screenshot(options?: { path?: string; timeout?: number; animations?: 'disabled' | 'allow' }): Promise<Buffer>;
  /** The content document of an `<iframe>` element handle, or null if not a frame. */
  contentFrame(): Promise<PlaywrightFrame | null>;
  /** Element box in page CSS pixels, or null if not rendered. */
  boundingBox(): Promise<BoundingBoxRect | null>;
  /** Scroll the element into view if it isn't already. */
  scrollIntoViewIfNeeded(options?: { timeout?: number }): Promise<void>;
  /** Read an attribute, or null if absent. */
  getAttribute(name: string): Promise<string | null>;
  /** Whether the element is visible. */
  isVisible(): Promise<boolean>;
  /** The element's text content, or null. */
  textContent(): Promise<string | null>;
}

/**
 * Structural subset of Playwright's `Frame` (an iframe's content document).
 */
export interface PlaywrightFrame {
  /** First matching element handle, or null. */
  $(selector: string): Promise<PlaywrightElementHandle | null>;
  /** Wait for a selector to reach the given state; resolves to the handle (or null). */
  waitForSelector(
    selector: string,
    options?: { state?: 'attached' | 'detached' | 'visible' | 'hidden'; timeout?: number },
  ): Promise<PlaywrightElementHandle | null>;
  /** Poll a predicate evaluated in the page until it returns truthy. */
  waitForFunction(
    pageFunction: Function | string,
    arg?: any,
    options?: { timeout?: number; polling?: number | 'raf' },
  ): Promise<unknown>;
}

/**
 * Structural subset of Playwright's `Page` — exactly the members `solve()` and
 * its helpers call. Any real Playwright `Page` satisfies this.
 */
export interface PlaywrightPage {
  /** Low-level mouse control used to drive human-like trajectories and clicks. */
  mouse: {
    move(x: number, y: number, options?: { steps?: number }): Promise<void>;
    down(options?: { button?: 'left' | 'right' | 'middle'; clickCount?: number }): Promise<void>;
    up(options?: { button?: 'left' | 'right' | 'middle'; clickCount?: number }): Promise<void>;
  };
  /** Sleep `timeout` ms (Playwright's own timer). */
  waitForTimeout(timeout: number): Promise<void>;
  /** Wait for a selector to reach the given state; resolves to the handle (or null). */
  waitForSelector(
    selector: string,
    options?: { state?: 'attached' | 'detached' | 'visible' | 'hidden'; timeout?: number },
  ): Promise<PlaywrightElementHandle | null>;
  /** Current viewport size, or null if not set. */
  viewportSize(): ViewportSize | null;
  /** First matching element handle, or null. */
  $(selector: string): Promise<PlaywrightElementHandle | null>;
  /** All matching element handles. */
  $$(selector: string): Promise<PlaywrightElementHandle[]>;
  /** Run `pageFunction` against the first matching element, in the page context. */
  $eval<R>(
    selector: string,
    pageFunction: (element: Element) => R,
    arg?: any,
  ): Promise<R>;
}

/**
 * Public alias. This is the type the solver's `solve(page)` accepts — kept under
 * a friendly name so consumers can annotate against it. It is the
 * implementation-neutral Playwright `Page`; pass a page from whichever
 * Playwright-compatible launcher you prefer.
 */
export type Page = PlaywrightPage;
