/**
 * How the driver moves: one pluggable object per input device. Mirrors python/humanize.py.
 *
 * The pointer position lives here, not in the solver, because a touch mode that dispatches no
 * motion between taps still has to say where the next gesture starts.
 */

import type { Page, PlaywrightElementHandle } from './playwright-types.js';
import { generateTrajectory } from 'cursory-js';

export type Point = [number, number];

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, Math.max(0, ms)));
const log = (m: string) => console.log(`[captchakraken] ${m}`);
const uniform = (lo: number, hi: number) => lo + Math.random() * (hi - lo);
const samePoint = (a: Point, b: Point) => Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6;
const gauss = (sigma: number) => {
  let u = 0;
  while (u === 0) u = Math.random();
  return sigma * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * Math.random());
};
const closedError = (e: unknown) => /Target closed|Session closed/.test(e instanceof Error ? e.message : String(e));

/** A recorded human movement morphed onto the endpoints; timings are cumulative ms. */
export function trajectory(start: Point, end: Point, frequency: number): [Point[], number[]] {
  const { points, timings } = generateTrajectory(start, end, { frequency });
  return [points.map((q) => [Number(q[0]), Number(q[1])] as Point), timings.map(Number)];
}

/** Every inter-gesture wait the driver takes, named, so each device supplies its own table. */
export type PauseKind = 'tap' | 'between' | 'grab' | 'drop' | 'probe' | 'settle' | 'key';
export const PAUSE_KINDS: readonly PauseKind[] = ['tap', 'between', 'grab', 'drop', 'probe', 'settle', 'key'] as const;

export interface Humanizer {
  readonly name: string;
  /** False on a device with no resting cursor; switches off every hover-for-realism behaviour. */
  readonly hovers: boolean;
  at: Point;
  reset(page: Page): Promise<void>;
  move(page: Page, to: Point): Promise<void>;
  press(page: Page): Promise<void>;
  release(page: Page): Promise<void>;
  click(page: Page, to: Point): Promise<void>;
  drag(page: Page, src: Point, dst: Point): Promise<void>;
  typeText(page: Page, field: PlaywrightElementHandle, text: string): Promise<boolean>;
  pause(kind: PauseKind): Promise<void>;
}

type PauseTable = Record<string, [number, number]>;

export abstract class BaseHumanizer implements Humanizer {
  abstract readonly name: string;
  abstract readonly hovers: boolean;
  protected abstract readonly pauses: PauseTable;
  at: Point;

  constructor(start: Point = [0, 0]) {
    this.at = [Number(start[0]), Number(start[1])];
  }

  async reset(_page: Page): Promise<void> {}
  abstract move(page: Page, to: Point): Promise<void>;
  abstract press(page: Page): Promise<void>;
  abstract release(page: Page): Promise<void>;
  abstract typeText(page: Page, field: PlaywrightElementHandle, text: string): Promise<boolean>;

  protected pauseMs(kind: PauseKind): number {
    const range = this.pauses[kind];
    return range ? uniform(range[0], range[1]) : 0;
  }

  async pause(kind: PauseKind): Promise<void> {
    await delay(this.pauseMs(kind));
  }

  async click(page: Page, to: Point): Promise<void> {
    await this.move(page, to);
    await this.press(page);
    await this.pause('tap');
    await this.release(page);
  }

  async drag(page: Page, src: Point, dst: Point): Promise<void> {
    await this.move(page, src);
    await this.press(page);
    await this.pause('grab');
    await this.move(page, dst);
    await this.pause('drop');
    await this.release(page);
  }
}

export class MouseHumanizer extends BaseHumanizer {
  readonly name = 'mouse';
  readonly hovers = true;
  protected readonly pauses: PauseTable = {
    tap: [20, 50], between: [80, 160], grab: [50, 100], drop: [50, 100],
    probe: [40, 80], settle: [90, 210], key: [45, 135],
  };
  private frequency: number;

  constructor(start: Point = [0, 0], frequency = 60) {
    super(start);
    this.frequency = frequency;
  }

  async move(page: Page, to: Point): Promise<void> {
    if (samePoint(this.at, to)) return;
    const [points, timings] = trajectory(this.at, to, this.frequency);
    let viewport = { width: 1920, height: 1080 };
    try {
      viewport = page.viewportSize() ?? viewport;
    } catch { /* keep the default */ }
    const startTime = Date.now();
    for (let i = 0; i < points.length; i++) {
      const cx = Math.max(0, Math.min(points[i][0], viewport.width));
      const cy = Math.max(0, Math.min(points[i][1], viewport.height));
      try {
        await page.mouse.move(cx, cy);
      } catch (e) {
        if (closedError(e)) { log('could not move mouse; page or session closed'); return; }
        continue;
      }
      this.at = [cx, cy];
      if (timings[i] !== undefined) await delay(startTime + timings[i] - Date.now());
    }
  }

  async press(page: Page): Promise<void> { await page.mouse.down(); }
  async release(page: Page): Promise<void> { await page.mouse.up(); }

  async typeText(page: Page, _field: PlaywrightElementHandle, text: string): Promise<boolean> {
    try { await page.keyboard.press('Control+A'); } catch { /* the type below still replaces on most */ }
    for (const ch of text) {
      try {
        await page.keyboard.type(ch);
      } catch (e) {
        log(`could not type into the captcha field: ${e}`);
        return false;
      }
      await this.pause('key');
    }
    return true;
  }
}

/** `[x, y, dtMs]`: dt is the wait before that sample. */
export type TouchSample = [number, number, number];

/** Where touch events go. `move` takes a whole leg so a device driver can pace it itself. */
export interface TouchBackend {
  readonly name: string;
  down(x: number, y: number): Promise<void>;
  move(path: TouchSample[]): Promise<void>;
  up(x: number, y: number): Promise<void>;
}

/** `Input.dispatchTouchEvent` over CDP; Chromium-family pages launched with `hasTouch: true`. */
export class CdpTouchBackend implements TouchBackend {
  readonly name = 'cdp';
  private constructor(private session: any) {}

  static async open(page: Page): Promise<CdpTouchBackend> {
    try {
      return new CdpTouchBackend(await (page as any).context().newCDPSession(page));
    } catch (e) {
      throw new Error(
        `mobile humanisation needs touch dispatch, and this page offers none (${e}). ` +
        'Use a Chromium-family Playwright browser launched with hasTouch: true, ' +
        'or pass an Appium driver as CaptchaKrakenConfig.touchDriver.');
    }
  }

  private async send(kind: string, points: Array<[number, number]>): Promise<void> {
    // radius and force are what a real digitizer reports; a synthetic tap reports zero.
    await this.session.send('Input.dispatchTouchEvent', {
      type: kind,
      touchPoints: points.map(([x, y]) => ({
        x, y, radiusX: uniform(8, 14), radiusY: uniform(8, 14), force: uniform(0.35, 0.75), id: 1,
      })),
    });
  }

  async down(x: number, y: number): Promise<void> { await this.send('touchStart', [[x, y]]); }
  async move(path: TouchSample[]): Promise<void> {
    for (const [x, y, dt] of path) {
      if (dt > 0) await delay(dt);
      await this.send('touchMove', [[x, y]]);
    }
  }
  async up(_x: number, _y: number): Promise<void> { await this.send('touchEnd', []); }
}

/** CSS-pixel to device-pixel transform for a real handset. */
export interface TouchTransform {
  scale?: number;
  origin?: Point;
}

/** W3C touch pointer actions for Appium and WebdriverIO, paced by the device from one chain per leg. */
export class AppiumTouchBackend implements TouchBackend {
  readonly name = 'appium';
  private scale: number;
  private origin: Point;
  private scaleGiven: boolean;
  private checked = false;

  constructor(private driver: any, transform: TouchTransform = {}, private page?: Page) {
    this.scaleGiven = transform.scale !== undefined;
    this.scale = transform.scale ?? 1;
    this.origin = transform.origin ?? [0, 0];
  }

  /** Refuse an unset scale on a device whose pixel ratio is not 1: the finger would land elsewhere. */
  private async checkScale(): Promise<void> {
    this.checked = true;
    if (this.scaleGiven || !this.page) return;
    let dpr: number;
    try {
      dpr = Number(await (this.page as any).evaluate('() => window.devicePixelRatio'));
    } catch { return; }
    if (!Number.isFinite(dpr) || Math.abs(dpr - 1) < 1e-6) return;
    throw new Error(
      `the touch driver maps CSS pixels onto a device reporting devicePixelRatio ${dpr}, and no scale ` +
      `was given. Fix: touchTransform: { scale: ${dpr}, origin: [x, y] }, where origin is the webview's ` +
      'top-left in SCREEN coordinates, or pass scale: 1 to assert the coordinates are already mapped.');
  }

  private map(x: number, y: number): [number, number] {
    return [Math.round(this.origin[0] + x * this.scale), Math.round(this.origin[1] + y * this.scale)];
  }

  private async perform(actions: any[]): Promise<void> {
    const chain = [{ type: 'pointer', id: 'ck-finger', parameters: { pointerType: 'touch' }, actions }];
    if (typeof this.driver.performActions === 'function') return this.driver.performActions(chain);
    if (typeof this.driver.execute === 'function') return this.driver.execute('actions', { actions: chain });
    throw new Error("the touch driver speaks neither performActions() nor execute('actions', …); " +
      'pass a WebDriver-compatible driver or a custom TouchBackend.');
  }

  async down(x: number, y: number): Promise<void> {
    if (!this.checked) await this.checkScale();
    const [mx, my] = this.map(x, y);
    await this.perform([
      { type: 'pointerMove', duration: 0, origin: 'viewport', x: mx, y: my },
      { type: 'pointerDown', button: 0 },
    ]);
  }

  async move(path: TouchSample[]): Promise<void> {
    if (!this.checked) await this.checkScale();
    if (!path.length) return;
    await this.perform(path.map(([x, y, dt]) => {
      const [mx, my] = this.map(x, y);
      return { type: 'pointerMove', duration: Math.max(0, Math.round(dt)), origin: 'viewport', x: mx, y: my };
    }));
  }

  async up(_x: number, _y: number): Promise<void> {
    if (!this.checked) await this.checkScale();
    await this.perform([{ type: 'pointerUp', button: 0 }]);
  }
}

/** Playwright's tap-only touchscreen, for browsers with touch but no CDP. A drag cannot be expressed. */
export class TouchscreenTouchBackend implements TouchBackend {
  readonly name = 'touchscreen';
  private pending: Point | null = null;
  constructor(private page: any) {}

  async down(x: number, y: number): Promise<void> { this.pending = [x, y]; }
  async move(_path: TouchSample[]): Promise<void> {
    throw new Error('this browser exposes taps but not touch travel, so a drag/slide puzzle cannot be ' +
      'driven on it. Use a Chromium-family browser (CDP touch dispatch) or an Appium driver.');
  }
  async up(x: number, y: number): Promise<void> {
    const at = this.pending ?? [x, y];
    this.pending = null;
    await this.page.touchscreen.tap(at[0], at[1]);
  }
}

export async function touchBackendFor(page: Page, driver?: any, transform?: TouchTransform): Promise<TouchBackend> {
  if (driver) {
    if (typeof driver.down === 'function' && typeof driver.up === 'function') return driver as TouchBackend;
    return new AppiumTouchBackend(driver, transform, page);
  }
  try {
    return await CdpTouchBackend.open(page);
  } catch (e) {
    if ((page as any).touchscreen) {
      log('no CDP session; falling back to tap-only touch dispatch');
      return new TouchscreenTouchBackend(page);
    }
    throw e;
  }
}

/** A finger on glass: no hover, touch events only, slower and more variable pauses. */
export class MobileHumanizer extends BaseHumanizer {
  readonly name = 'mobile';
  readonly hovers = false;
  protected readonly pauses: PauseTable = {
    tap: [55, 130], between: [140, 320], grab: [90, 190], drop: [80, 170],
    probe: [70, 140], settle: [140, 300], key: [110, 320],
  };
  private backend: TouchBackend | null;
  private driver: any;
  private transform: TouchTransform | undefined;
  private frequency: number;
  private down = false;

  constructor(start: Point = [0, 0],
              options: { backend?: TouchBackend; driver?: any; transform?: TouchTransform; frequency?: number } = {}) {
    super(start);
    this.backend = options.backend ?? null;
    this.driver = options.driver;
    this.transform = options.transform;
    this.frequency = options.frequency ?? 90;
  }

  async reset(page: Page): Promise<void> {
    if (!this.down) return;
    try { await (await this.touch(page)).up(this.at[0], this.at[1]); } catch { /* a stale pointer must not fail a solve */ }
    this.down = false;
  }

  private async touch(page: Page): Promise<TouchBackend> {
    if (!this.backend) this.backend = await touchBackendFor(page, this.driver, this.transform);
    return this.backend;
  }

  async move(page: Page, to: Point): Promise<void> {
    const dest: Point = [Number(to[0]), Number(to[1])];
    if (this.down) {
      const [points, timings] = trajectory(this.at, dest, this.frequency);
      await (await this.touch(page)).move(points.map(([x, y], i) => [x, y, timings[i] - (i ? timings[i - 1] : 0)]));
    }
    this.at = dest;
  }

  async press(page: Page): Promise<void> {
    await (await this.touch(page)).down(this.at[0], this.at[1]);
    this.down = true;
  }

  async release(page: Page): Promise<void> {
    await (await this.touch(page)).up(this.at[0], this.at[1]);
    this.down = false;
  }

  /** A tap whose contact patch wobbles a pixel while held; a motionless tap is a synthetic one. */
  async click(page: Page, to: Point): Promise<void> {
    await this.move(page, to);
    await this.press(page);
    const held = this.pauseMs('tap');
    await delay(held / 2);
    try {
      await (await this.touch(page)).move([[this.at[0] + gauss(0.9), this.at[1] + gauss(0.9), 0]]);
    } catch { /* a tap-only backend cannot wobble; the tap still lands */ }
    await delay(held / 2);
    await this.release(page);
  }

  async typeText(page: Page, field: PlaywrightElementHandle, text: string): Promise<boolean> {
    const f = field as any;
    for (const [name, arg] of [['clear', undefined], ['fill', '']] as const) {
      if (typeof f?.[name] !== 'function') continue;
      try { await f[name](arg); break; } catch { /* an uncleared box is recoverable */ }
    }
    for (const ch of text) {
      try {
        if (typeof f?.sendKeys === 'function') await f.sendKeys(ch);
        else await page.keyboard.type(ch);
      } catch (e) {
        log(`could not type into the captcha field: ${e}`);
        return false;
      }
      await this.pause('key');
    }
    return true;
  }
}

/** No humanisation: one move per gesture and a single fill. Fast, and detectable. */
export class NullHumanizer extends BaseHumanizer {
  readonly name = 'none';
  readonly hovers = false;
  protected readonly pauses: PauseTable = {};

  async move(page: Page, to: Point): Promise<void> {
    if (samePoint(this.at, to)) return;
    this.at = [Number(to[0]), Number(to[1])];
    try {
      await page.mouse.move(this.at[0], this.at[1]);
    } catch (e) {
      if (closedError(e)) log('could not move mouse; page or session closed');
    }
  }

  async press(page: Page): Promise<void> { await page.mouse.down(); }
  async release(page: Page): Promise<void> { await page.mouse.up(); }

  async typeText(page: Page, field: PlaywrightElementHandle, text: string): Promise<boolean> {
    try { await (field as any).fill(text); return true; } catch { /* fall through to the keyboard */ }
    try { await page.keyboard.press('Control+A'); } catch { /* optional */ }
    try {
      await page.keyboard.type(text);
      return true;
    } catch (e) {
      log(`could not type into the captcha field: ${e}`);
      return false;
    }
  }
}

export type HumanizationMode = 'mouse' | 'mobile' | 'none';
export const MODES: readonly HumanizationMode[] = ['mouse', 'mobile', 'none'] as const;

export interface HumanizerOptions {
  humanization?: HumanizationMode;
  humanizer?: Humanizer;
  touchDriver?: any;
  touchTransform?: TouchTransform;
  startingMousePosition?: { x: number; y: number };
}

/** `config.humanizer`, else `config.humanization`, else CAPTCHA_HUMANIZATION, else mouse. */
export function resolveHumanizer(config: HumanizerOptions = {}): Humanizer {
  if (config.humanizer) return config.humanizer;
  const raw = (config.humanization ?? process.env.CAPTCHA_HUMANIZATION ?? 'mouse').toString().trim().toLowerCase() as HumanizationMode;
  if (!MODES.includes(raw)) {
    throw new Error(`unknown humanization mode '${raw}'; expected one of ${MODES.join(', ')}, ` +
      'or pass your own object as CaptchaKrakenConfig.humanizer');
  }
  const p = config.startingMousePosition;
  const start: Point = p ? [p.x, p.y] : [0, 0];
  if (raw === 'mobile') return new MobileHumanizer(start, { driver: config.touchDriver, transform: config.touchTransform });
  if (raw === 'none') return new NullHumanizer(start);
  return new MouseHumanizer(start);
}
