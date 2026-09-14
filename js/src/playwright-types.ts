export interface BoundingBoxRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ViewportSize {
  width: number;
  height: number;
}

export interface PlaywrightElementHandle {
  screenshot(options?: { path?: string; timeout?: number; animations?: 'disabled' | 'allow' }): Promise<Buffer>;

  contentFrame(): Promise<PlaywrightFrame | null>;

  boundingBox(): Promise<BoundingBoxRect | null>;

  scrollIntoViewIfNeeded(options?: { timeout?: number }): Promise<void>;

  getAttribute(name: string): Promise<string | null>;

  isVisible(): Promise<boolean>;

  textContent(): Promise<string | null>;

  $(selector: string): Promise<PlaywrightElementHandle | null>;

  $$(selector: string): Promise<PlaywrightElementHandle[]>;
}

export interface PlaywrightFrame {
  $(selector: string): Promise<PlaywrightElementHandle | null>;

  $$(selector: string): Promise<PlaywrightElementHandle[]>;

  waitForSelector(
    selector: string,
    options?: { state?: 'attached' | 'detached' | 'visible' | 'hidden'; timeout?: number },
  ): Promise<PlaywrightElementHandle | null>;

  waitForFunction(
    pageFunction: Function | string,
    arg?: any,
    options?: { timeout?: number; polling?: number | 'raf' },
  ): Promise<unknown>;
}

/**
 * Structural on purpose: the package depends on no browser library, and the version skew between
 * playwright, patchright and camoufox-js makes a nominal import the wrong one for someone.
 */
export interface PlaywrightPage {
  mouse: {
    move(x: number, y: number, options?: { steps?: number }): Promise<void>;
    down(options?: { button?: 'left' | 'right' | 'middle'; clickCount?: number }): Promise<void>;
    up(options?: { button?: 'left' | 'right' | 'middle'; clickCount?: number }): Promise<void>;
  };

  /** `type` is called per character by the humanizer; a constant `delay` would itself be a signal. */
  keyboard: {
    type(text: string, options?: { delay?: number }): Promise<void>;
    press(key: string, options?: { delay?: number }): Promise<void>;
  };

  waitForTimeout(timeout: number): Promise<void>;

  waitForSelector(
    selector: string,
    options?: { state?: 'attached' | 'detached' | 'visible' | 'hidden'; timeout?: number },
  ): Promise<PlaywrightElementHandle | null>;

  viewportSize(): ViewportSize | null;

  context?(): { newCDPSession(page: PlaywrightPage): Promise<any> };

  touchscreen?: {
    tap(x: number, y: number): Promise<void>;
  };

  $(selector: string): Promise<PlaywrightElementHandle | null>;

  $$(selector: string): Promise<PlaywrightElementHandle[]>;

  isClosed?(): boolean;

  $eval<R>(
    selector: string,
    pageFunction: (element: Element) => R,
    arg?: any,
  ): Promise<R>;
}

export type Page = PlaywrightPage;
