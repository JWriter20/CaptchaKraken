import {
  PlaywrightPage,
  PlaywrightFrame,
  PlaywrightElementHandle,
  BoundingBoxRect,
  ViewportSize,
} from './playwright-types';

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
  $(selector: string): Promise<PuppeteerElementHandle | null>;
  $$(selector: string): Promise<PuppeteerElementHandle[]>;
}
interface PuppeteerFrame {
  $(selector: string): Promise<PuppeteerElementHandle | null>;
  $$(selector: string): Promise<PuppeteerElementHandle[]>;
  waitForSelector(selector: string, options?: any): Promise<PuppeteerElementHandle | null>;
  waitForFunction(pageFunction: Function | string, options?: any, ...args: any[]): Promise<unknown>;
}
interface PuppeteerPage {
  mouse: {
    move(x: number, y: number, options?: { steps?: number }): Promise<void>;
    down(options?: any): Promise<void>;
    up(options?: any): Promise<void>;
  };
  keyboard: {
    type(text: string, options?: { delay?: number }): Promise<void>;
    press(key: string, options?: any): Promise<void>;
    down(key: string, options?: any): Promise<void>;
    up(key: string): Promise<void>;
  };
  waitForSelector(selector: string, options?: any): Promise<PuppeteerElementHandle | null>;
  viewport(): ViewportSize | null;
  evaluate<R>(pageFunction: () => R): Promise<R>;
  $(selector: string): Promise<PuppeteerElementHandle | null>;
  $$(selector: string): Promise<PuppeteerElementHandle[]>;
  $eval(selector: string, pageFunction: (element: Element) => any, ...args: any[]): Promise<any>;
  isClosed(): boolean;
}

function toPuppeteerSelectorOptions(options?: PuppeteerSelectorState): any {
  if (!options) return undefined;
  const { state, timeout } = options;
  const out: any = {};
  if (timeout !== undefined) out.timeout = timeout;
  if (state === 'visible') out.visible = true;
  else if (state === 'hidden') out.hidden = true;

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
    $: async (selector) => wrapHandle(await h.$(selector)),

    $$: async (selector) =>
      (await h.$$(selector)).map(wrapHandle).filter((x): x is PlaywrightElementHandle => !!x),
  };
}

function wrapFrame(f: PuppeteerFrame | null): PlaywrightFrame | null {
  if (!f) return null;
  return {
    $: async (selector) => wrapHandle(await f.$(selector)),
    $$: async (selector) =>
      (await f.$$(selector)).map(wrapHandle).filter((x): x is PlaywrightElementHandle => !!x),
    waitForSelector: async (selector, options) =>
      wrapHandle(await f.waitForSelector(selector, toPuppeteerSelectorOptions(options))),
    waitForFunction: (pageFunction, arg, options) =>

      f.waitForFunction(pageFunction as any, options, arg),
  };
}

export function fromPuppeteer(page: PuppeteerPage): PlaywrightPage {
  return {
    mouse: {
      move: (x, y, options) => page.mouse.move(x, y, options),
      down: (options) => page.mouse.down(options),
      up: (options) => page.mouse.up(options),
    },
    keyboard: {
      type: (text, options) => page.keyboard.type(text, options),

      // Puppeteer has no 'Control+A' combo syntax; the solver speaks Playwright and this side translates.
      press: async (key) => {
        const parts = key.split('+');
        const target = parts.pop() as string;
        for (const mod of parts) await page.keyboard.down(mod);
        await page.keyboard.press(target);
        for (const mod of parts.reverse()) await page.keyboard.up(mod);
      },
    },
    waitForTimeout: (timeout) => new Promise<void>((resolve) => setTimeout(resolve, timeout)),
    waitForSelector: async (selector, options) =>
      wrapHandle(await page.waitForSelector(selector, toPuppeteerSelectorOptions(options))),
    viewportSize: () => page.viewport(),
    evaluate: (pageFunction) => page.evaluate(pageFunction),
    $: async (selector) => wrapHandle(await page.$(selector)),
    $$: async (selector) => (await page.$$(selector)).map((h) => wrapHandle(h)!).filter(Boolean),
    $eval: (selector, pageFunction, arg) => page.$eval(selector, pageFunction as any, arg),
    // Forwarded explicitly: without it the watcher polls a dead page forever.
    isClosed: () => page.isClosed(),
  };
}
