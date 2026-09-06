/**
 * How the driver MOVES. One pluggable object per input device.
 *
 * The TypeScript side of `python/src/captchakraken/humanize.py`; the two are one
 * design in two languages, and `tests/test_humanizer_parity.py` in
 * CaptchaKrakenFinetune pins that the mode names and the pause vocabulary have
 * not drifted.
 *
 * Humanisation used to be wired straight into `solver.ts`: every gesture was a
 * `page.mouse.*` call with a Bezier trajectory in front of it and a `Math.
 * random()` sleep behind it, and there was no way to ask for anything else. That
 * is wrong in three directions at once —
 *
 *   - a caller driving a MOBILE page has no cursor. Dispatching mousemove at a
 *     touch-only widget is not weak humanisation, it is the wrong event type:
 *     the page's touch handlers never fire, and a vendor that scores pointer
 *     telemetry sees a desktop mouse on a phone.
 *   - a caller who has their OWN humanisation (a hardware pointer, a proxied
 *     device farm, a model of their own users) was composing two of them. That
 *     is not hypothetical — camoufox's `humanize` juggler re-humanises every
 *     `mouse.move()` it is handed, and running both measured 82.1s against
 *     13.4s on one geetest_v4_slide solve, because each of the 60 trajectory
 *     points became its own humanised sub-trajectory.
 *   - a caller on their own infrastructure, against their own fixtures, is
 *     paying for texture nobody is measuring.
 *
 * So the vocabulary of gestures is an INTERFACE and the humanisation is an
 * implementation of it. The solver says "tap here", "drag this there", "type
 * that"; a `Humanizer` decides what events that is and how long it takes.
 *
 * Four modes, selected by `CaptchaKrakenConfig.humanization`:
 *
 *     'mouse'   MouseHumanizer   — the default, and byte-for-byte what the
 *                                  driver did before this module existed.
 *     'mobile'  MobileHumanizer  — touch events, finger kinematics. Dispatches
 *                                  through a `TouchBackend`, so it drives either
 *                                  a Chromium-family page (CDP) or an
 *                                  Appium/WebdriverIO driver on a real device.
 *     'none'    NullHumanizer    — the shortest legal path to the same DOM
 *                                  effect.
 *     custom    anything satisfying `Humanizer`, passed as
 *                                  `CaptchaKrakenConfig.humanizer`.
 *
 * THE POINTER POSITION LIVES HERE, not in the solver. A humanizer that
 * dispatches no motion at all (mobile, between taps) still has to answer "where
 * is the pointer", because that is what the next gesture starts from and what
 * `SolveResult.finalMousePosition` reports.
 */

import type { Page, PlaywrightElementHandle } from './playwright-types.js';
import { generate_swipe, generate_trajectory } from './trajectory.js';

export type Point = [number, number];

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, Math.max(0, ms)));
const log = (m: string) => console.log(`[captchakraken] ${m}`);
const uniform = (lo: number, hi: number) => lo + Math.random() * (hi - lo);

/**
 * A move to where the pointer already is emits nothing.
 *
 * `moveAndClick` travels to the element and THEN clicks at the point it landed
 * on, so without this every click would dispatch one redundant move at the
 * coordinate the trajectory just finished on.
 */
const samePoint = (a: Point, b: Point) =>
  Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6;

/** Box-Muller, as in trajectory.ts — the contact wobble wants a normal deviate. */
function gauss(mu: number, sigma: number): number {
  let u = 0;
  while (u === 0) u = Math.random();
  const v = Math.random();
  return mu + sigma * Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
}

/**
 * Every inter-gesture wait the driver takes, named.
 *
 * A vocabulary rather than a number at each call site, because the whole point
 * of the mode switch is that these differ per device — a finger dwells on a tap
 * about four times as long as a mouse button is held, and neither number means
 * anything to the caller who turned humanisation off. Each mode supplies its own
 * table below; an unknown name yields no wait rather than throwing, so adding a
 * pause site cannot break a custom humanizer written against an older release.
 *
 *   tap      inside a click/tap, between press and release
 *   between  between two taps of one batch (grid tiles)
 *   grab     after pressing, before a drag starts moving
 *   drop     after a drag arrives, before releasing
 *   probe    between the slider's measurement nudges
 *   settle   before releasing a slider — the release IS the submit, and some
 *            vendors sample the final milliseconds of the gesture
 *   key      between two typed characters
 */
export type PauseKind = 'tap' | 'between' | 'grab' | 'drop' | 'probe' | 'settle' | 'key';

export const PAUSE_KINDS: readonly PauseKind[] = [
  'tap',
  'between',
  'grab',
  'drop',
  'probe',
  'settle',
  'key',
] as const;

/**
 * The gesture vocabulary the solver drives. Implement this to supply your own
 * humanisation; only `move`, `press`, `release`, `typeText` and `pause` are
 * required, and `BaseHumanizer` composes `click`/`drag` from them.
 */
export interface Humanizer {
  /** Mode name, for logs. */
  readonly name: string;
  /**
   * Whether this device has a cursor that can rest somewhere without touching.
   * False switches off every hover-for-realism behaviour — the reCAPTCHA tile
   * hover and the idle drift during inference — because on a touchscreen those
   * are not weak mimicry, they are impossible.
   */
  readonly hovers: boolean;
  /** Where the pointer is now. */
  at: Point;
  /** Called once at the top of each `solve()`. Drop per-page caches. */
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

// ───────────────────────────────────────────────────────────────────── mouse

/**
 * A hand on a mouse. Bezier arcs, Fitts's-law durations, overshoot.
 *
 * Everything in here was `solver.performSmoothMove` / `tracePath` before this
 * module existed, and is unchanged: the constants were measured and a refactor
 * is not the place to revisit them.
 */
export class MouseHumanizer extends BaseHumanizer {
  readonly name = 'mouse';
  readonly hovers = true;

  /** Inclusive ranges, in ms. See PauseKind. */
  protected readonly pauses: PauseTable = {
    tap: [20, 50],
    between: [80, 160],
    grab: [50, 100],
    drop: [50, 100],
    probe: [40, 80],
    settle: [90, 210],
    key: [45, 135],
  };

  private frequency: number;

  constructor(start: Point = [0, 0], frequency = 60) {
    super(start);
    this.frequency = frequency;
  }

  async move(page: Page, to: Point): Promise<void> {
    if (samePoint(this.at, to)) return;
    const [points, timings] = generate_trajectory(this.at, to, this.frequency);
    await this.trace(page, points, timings);
  }

  async press(page: Page): Promise<void> {
    await page.mouse.down();
  }

  async release(page: Page): Promise<void> {
    await page.mouse.up();
  }

  async typeText(page: Page, _field: PlaywrightElementHandle, text: string): Promise<boolean> {
    // A retry round arrives with the previous attempt still in the box, and
    // typing would APPEND to it — submitting a string the model never read.
    try {
      await page.keyboard.press('Control+A');
    } catch {
      /* not every field supports it; the type below still replaces on most */
    }
    // Per character rather than one `type(text, {delay})` call: a constant
    // inter-key delay is itself a signal, and these are the vendors that score
    // typing cadence.
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

  private async trace(page: Page, points: Point[], timings: number[]): Promise<void> {
    // KNOWN DIVERGENCE from the Python port, carried over unchanged rather than
    // fixed inside a refactor. Python clamps only when the viewport is actually
    // known, because camoufox reports `viewportSize` null and a coordinate
    // pinned to the edge of a GUESSED viewport is what deadlocks its humanised-
    // mouse juggler (upstream #225) — see MouseHumanizer._viewport there. This
    // port still assumes 1920x1080. Worth fixing on its own, with its own test.
    let viewport: { width: number; height: number } = { width: 1920, height: 1080 };
    try {
      const vp = page.viewportSize();
      if (vp) viewport = vp;
    } catch {
      /* keep the default */
    }

    const startTime = Date.now();
    for (let i = 0; i < points.length; i++) {
      try {
        const cx = Math.max(0, Math.min(points[i][0], viewport.width));
        const cy = Math.max(0, Math.min(points[i][1], viewport.height));
        await page.mouse.move(cx, cy);
        this.at = [cx, cy];
        if (timings[i] !== undefined) {
          const wait = startTime + timings[i] - Date.now();
          if (wait > 0) await delay(wait);
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        if (msg.includes('Target closed') || msg.includes('Session closed')) {
          log('Warning: could not move mouse, page or session closed.');
          return;
        }
        // Any other per-sample failure is skipped rather than fatal — losing
        // one mousemove must not lose the solve.
      }
    }
  }
}

// ─────────────────────────────────────────────────────────── touch dispatch

/** `[x, y, dtMs]` — dt is the wait BEFORE that sample. */
export type TouchSample = [number, number, number];

/**
 * Where touch events actually go.
 *
 * Three methods rather than one per sample, because the two backends want
 * opposite things: CDP dispatches one event per call and we pace it locally,
 * while a W3C/Appium driver takes a whole timed action chain in one round trip
 * and paces it on the device. Handing `move` the entire leg lets each do what it
 * is good at — and on a real device, pacing over the wire is not pacing at all.
 */
export interface TouchBackend {
  readonly name: string;
  down(x: number, y: number): Promise<void>;
  move(path: TouchSample[]): Promise<void>;
  up(x: number, y: number): Promise<void>;
}

/**
 * `Input.dispatchTouchEvent` over a CDP session on a Playwright page.
 *
 * Chromium-family only, and that is checked at construction rather than
 * discovered per gesture: WebKit and Firefox (camoufox included) expose no touch
 * dispatch through Playwright at all, so the alternatives there are to fail
 * loudly or to quietly emit MOUSE events at a touch-only widget. The second is
 * worse than not running — the page's touch handlers never fire, the solve fails
 * for a reason nothing reports, and the report reads as a model that cannot
 * solve mobile puzzles.
 *
 * The context must also have been created with `hasTouch: true`, or the page
 * advertises no touch support to feature detection and a mobile widget renders
 * its desktop branch.
 */
export class CdpTouchBackend implements TouchBackend {
  readonly name = 'cdp';
  private session: any;

  private constructor(session: any) {
    this.session = session;
  }

  static async open(page: Page): Promise<CdpTouchBackend> {
    try {
      const context = (page as any).context();
      return new CdpTouchBackend(await context.newCDPSession(page));
    } catch (e) {
      throw new Error(
        `mobile humanisation needs touch dispatch, and this page offers none (${e}). ` +
          'Use a Chromium-family Playwright browser launched with hasTouch: true, ' +
          'or pass an Appium driver as CaptchaKrakenConfig.touchDriver.',
      );
    }
  }

  private async send(kind: string, points: Array<[number, number]>): Promise<void> {
    await this.session.send('Input.dispatchTouchEvent', {
      type: kind,
      touchPoints: points.map(([x, y]) => ({
        // radiusX/Y and force are what a real digitizer reports and a synthetic
        // tap does not. A vendor reading `Touch.radiusX === 0` has a free bot
        // signal otherwise.
        x,
        y,
        radiusX: uniform(8, 14),
        radiusY: uniform(8, 14),
        force: uniform(0.35, 0.75),
        id: 1,
      })),
    });
  }

  async down(x: number, y: number): Promise<void> {
    await this.send('touchStart', [[x, y]]);
  }

  async move(path: TouchSample[]): Promise<void> {
    for (const [x, y, dt] of path) {
      if (dt > 0) await delay(dt);
      await this.send('touchMove', [[x, y]]);
    }
  }

  async up(_x: number, _y: number): Promise<void> {
    // touchEnd carries no touchPoints: the point being lifted is identified by
    // its absence, which is what the protocol says and what a real release
    // looks like.
    await this.send('touchEnd', []);
  }
}

/** CSS-pixel → device-pixel transform for a real handset. See AppiumTouchBackend. */
export interface TouchTransform {
  scale?: number;
  origin?: Point;
}

/**
 * W3C pointer actions with `pointerType: 'touch'`, for Appium and WebdriverIO.
 *
 * Batches a whole leg into ONE action chain with per-sample durations, so the
 * kinematics are reproduced BY THE DEVICE rather than by a JS loop round-tripping
 * over the wire — over which a 90Hz path is not 90Hz at all.
 *
 * Press and release are separate `performActions()` calls on purpose. W3C input
 * state is per SESSION, so a pointer put down in one chain stays down across
 * later chains until it is lifted; that is what lets the puzzle-slider driver
 * press, screenshot, steer, screenshot and only then release. (It is also why
 * the spec has a "release actions" endpoint at all.)
 *
 * COORDINATES. Everything upstream of here is CSS pixels in the page's viewport,
 * because that is what `boundingBox()` returns. A real device wants screen
 * pixels, and the two differ by the device pixel ratio and by whatever chrome
 * sits above the webview. Neither is guessable from here, so both are options:
 *
 *     new AppiumTouchBackend(driver, { scale: 3, origin: [0, 132] })
 *
 * `scale` is usually `window.devicePixelRatio`; `origin` is the top-left of the
 * webview in screen coordinates. Defaults are the identity transform, which is
 * correct for browser mobile emulation and for any caller who has already mapped
 * the coordinates themselves.
 */
export class AppiumTouchBackend implements TouchBackend {
  readonly name = 'appium';
  private driver: any;
  private scale: number;
  private origin: Point;
  /**
   * UNSET is not the same fact as an explicit 1. The first is a caller who has
   * not thought about the transform; the second is one who has, and says the
   * coordinates are already mapped. Only the first is checked.
   */
  private scaleGiven: boolean;
  private page: Page | undefined;
  private checked = false;

  constructor(driver: any, transform: TouchTransform = {}, page?: Page) {
    this.driver = driver;
    this.scaleGiven = transform.scale !== undefined;
    this.scale = transform.scale ?? 1;
    this.origin = transform.origin ?? [0, 0];
    this.page = page;
  }

  /**
   * Refuse an unset scale on a session that reports it is not 1.
   *
   * A wrong transform fails SILENTLY on both sides of the wire: the chain is
   * valid W3C, the device performs it, and the finger lands somewhere else. The
   * solve then fails looking exactly like a model that cannot read the puzzle.
   *
   * Read once, not per gesture — it is a round trip into the page and a solve
   * makes hundreds of these. An unreadable ratio is absent evidence, not
   * evidence of a mismatch, so it falls through to the identity. `origin`
   * cannot be measured from here at all, so it is named in the refusal rather
   * than guessed at.
   */
  private async checkScale(): Promise<void> {
    this.checked = true;
    if (this.scaleGiven || !this.page) return;
    let dpr: number;
    try {
      dpr = Number(await (this.page as any).evaluate('() => window.devicePixelRatio'));
    } catch {
      return;  // cannot measure is not a mismatch
    }
    if (!Number.isFinite(dpr) || Math.abs(dpr - 1) < 1e-6) return;
    throw new Error(
      `the touch driver maps CSS pixels onto a device reporting devicePixelRatio ` +
      `${dpr}, and no scale was given. Every gesture would land at ` +
      `${(1 / dpr).toFixed(2)}x the intended offset, and nothing would report it — ` +
      `the chain is valid, the device performs it, and the solve fails looking ` +
      `like a weak model.\n` +
      `  Fix: touchTransform: { scale: ${dpr}, origin: [x, y] }, where origin is ` +
      `the webview's top-left in SCREEN coordinates. That half cannot be measured ` +
      `from here.\n` +
      `  Or pass scale: 1 to assert the coordinates are already mapped.`,
    );
  }

  private map(x: number, y: number): [number, number] {
    return [
      Math.round(this.origin[0] + x * this.scale),
      Math.round(this.origin[1] + y * this.scale),
    ];
  }

  /**
   * One W3C action chain on a single touch pointer.
   *
   * Sent as the raw protocol payload rather than through a client's action
   * builder, so this imports nothing and works against any client that speaks
   * WebDriver — WebdriverIO (`performActions`), selenium-webdriver, or a thin
   * HTTP wrapper of someone's own.
   */
  private async perform(actions: any[]): Promise<void> {
    const chain = [
      {
        type: 'pointer',
        id: 'ck-finger',
        parameters: { pointerType: 'touch' },
        actions,
      },
    ];
    if (typeof this.driver.performActions === 'function') {
      await this.driver.performActions(chain);
      return;
    }
    if (typeof this.driver.execute === 'function') {
      await this.driver.execute('actions', { actions: chain });
      return;
    }
    throw new Error(
      "the touch driver speaks neither performActions() nor execute('actions', …); " +
        'pass a WebDriver-compatible driver or a custom TouchBackend.',
    );
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
    await this.perform(
      path.map(([x, y, dt]) => {
        const [mx, my] = this.map(x, y);
        // The per-sample gap becomes the move's DURATION, not a pause before it:
        // the device then interpolates over that window and reports intermediate
        // samples of its own, which is what a finger sliding across a digitizer
        // actually produces.
        return { type: 'pointerMove', duration: Math.max(0, Math.round(dt)), origin: 'viewport', x: mx, y: my };
      }),
    );
  }

  async up(_x: number, _y: number): Promise<void> {
    if (!this.checked) await this.checkScale();
    await this.perform([{ type: 'pointerUp', button: 0 }]);
  }
}

/**
 * Playwright's `page.touchscreen.tap()` — taps only, no travel.
 *
 * The fallback for a browser with touch support but no CDP (WebKit). A tap still
 * lands correctly; a DRAG cannot be expressed at all, so it throws rather than
 * approximating one with a mouse, for the reason in `CdpTouchBackend`.
 */
export class TouchscreenTouchBackend implements TouchBackend {
  readonly name = 'touchscreen';
  private page: any;
  private pending: Point | null = null;

  constructor(page: Page) {
    this.page = page;
  }

  async down(x: number, y: number): Promise<void> {
    this.pending = [x, y];
  }

  async move(_path: TouchSample[]): Promise<void> {
    throw new Error(
      'this browser exposes taps but not touch travel, so a drag/slide puzzle ' +
        'cannot be driven on it. Use a Chromium-family browser (CDP touch dispatch) ' +
        'or an Appium driver.',
    );
  }

  async up(x: number, y: number): Promise<void> {
    const at = this.pending ?? [x, y];
    this.pending = null;
    await this.page.touchscreen.tap(at[0], at[1]);
  }
}

/**
 * Pick a backend. An explicit `driver` always wins.
 *
 * `driver` is what a caller passes when the thing under automation is not the
 * thing being touched — an Appium session driving a real handset while the page
 * object is a webview bridge over it.
 */
export async function touchBackendFor(
  page: Page,
  driver?: any,
  transform?: TouchTransform,
): Promise<TouchBackend> {
  if (driver) {
    if (typeof driver.down === 'function' && typeof driver.up === 'function') {
      return driver as TouchBackend;
    }
    // The page comes along so the backend can ask what it is mapping ONTO.
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

// ──────────────────────────────────────────────────────────────────── mobile

/**
 * A finger on glass.
 *
 * What differs from `MouseHumanizer`, and none of it is cosmetic:
 *
 *   - **A move that is not touching dispatches nothing.** There is no hover on a
 *     touchscreen. The position is still RECORDED, because the next gesture
 *     starts from it, but no event is emitted — emitting one would be the
 *     desktop tell this mode exists to remove.
 *   - **Taps carry a contact wobble.** A finger held on glass for 90ms does not
 *     report one unchanging coordinate; the centroid of the contact patch rolls
 *     a pixel or two under pressure. A tap with zero movement between touchstart
 *     and touchend is a synthetic tap.
 *   - **Touch kinematics**, via `generate_swipe` — see its docstring for why that
 *     is a different model rather than the mouse one retuned.
 *   - **Longer, more variable pauses.** Every measured touch interaction is
 *     slower than its mouse equivalent, and a phone's are more variable because
 *     the hand holding the device is also moving.
 */
export class MobileHumanizer extends BaseHumanizer {
  readonly name = 'mobile';
  readonly hovers = false;

  protected readonly pauses: PauseTable = {
    // A tap's press-to-release. Measured human touch dwell clusters at
    // 60-120ms; a synthesised one is usually 0.
    tap: [55, 130],
    between: [140, 320],
    grab: [90, 190],
    drop: [80, 170],
    probe: [70, 140],
    settle: [140, 300],
    // Soft-keyboard typing, which is ~3x slower than a physical keyboard and
    // much more variable.
    key: [110, 320],
  };

  private backend: TouchBackend | null;
  private driver: any;
  private transform: TouchTransform | undefined;
  private frequency: number;
  private down = false;

  constructor(
    start: Point = [0, 0],
    options: { backend?: TouchBackend; driver?: any; transform?: TouchTransform; frequency?: number } = {},
  ) {
    super(start);
    this.backend = options.backend ?? null;
    this.driver = options.driver;
    this.transform = options.transform;
    this.frequency = options.frequency ?? 90;
  }

  async reset(page: Page): Promise<void> {
    // A solve that ended mid-gesture (a timeout inside the slider) leaves a
    // pointer down in the SESSION's input state, and the next chain would then
    // start from a finger already on the glass. Lift it.
    if (this.down) {
      try {
        await (await this.touch(page)).up(this.at[0], this.at[1]);
      } catch {
        /* best effort; a stale pointer must not fail a solve */
      }
      this.down = false;
    }
  }

  private async touch(page: Page): Promise<TouchBackend> {
    if (!this.backend) {
      this.backend = await touchBackendFor(page, this.driver, this.transform);
    }
    return this.backend;
  }

  async move(page: Page, to: Point): Promise<void> {
    const dest: Point = [Number(to[0]), Number(to[1])];
    if (!this.down) {
      // No contact, no events. Just remember where the next touch lands.
      this.at = dest;
      return;
    }
    const [points, timings] = generate_swipe(this.at, dest, this.frequency);
    const path: TouchSample[] = points.map(([x, y], i) => [
      x,
      y,
      timings[i] - (i ? timings[i - 1] : 0),
    ]);
    await (await this.touch(page)).move(path);
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

  /** Tap. The wobble is the point — see the class docstring. */
  async click(page: Page, to: Point): Promise<void> {
    await this.move(page, to);
    await this.press(page);
    const held = this.pauseMs('tap');
    await delay(held / 2);
    try {
      await (await this.touch(page)).move([
        [this.at[0] + gauss(0, 0.9), this.at[1] + gauss(0, 0.9), 0],
      ]);
    } catch {
      /* a tap-only backend cannot wobble; the tap still lands */
    }
    await delay(held / 2);
    await this.release(page);
  }

  /**
   * Into the field, at soft-keyboard pace.
   *
   * The mouse path clears with Control+A. There is no Control on a phone
   * keyboard, so the box is cleared through the element instead — which also
   * works on an Appium element, where `page.keyboard` does not exist.
   */
  async typeText(page: Page, field: PlaywrightElementHandle, text: string): Promise<boolean> {
    const f = field as any;
    for (const [name, arg] of [['clear', undefined], ['fill', '']] as const) {
      if (typeof f?.[name] !== 'function') continue;
      try {
        await f[name](arg);
        break;
      } catch {
        /* an uncleared box is recoverable; a crash is not */
      }
    }
    for (const ch of text) {
      try {
        if (typeof f?.sendKeys === 'function') await f.sendKeys(ch);   // Appium / WebDriver
        else await page.keyboard.type(ch);                             // mobile emulation
      } catch (e) {
        log(`could not type into the captcha field: ${e}`);
        return false;
      }
      await this.pause('key');
    }
    return true;
  }
}

// ────────────────────────────────────────────────────────────────────── none

/**
 * No humanisation: the shortest legal path to the same DOM effect.
 *
 * One mousemove per gesture instead of sixty, no dwell, no jitter, and text goes
 * in with a single `fill()`. Roughly an order of magnitude faster on a slider
 * puzzle, and it will be detected by anything that scores pointer telemetry —
 * which is the whole trade, stated plainly. For fixtures, for self-hosted
 * targets, and for callers whose stack humanises somewhere else.
 *
 * Still moves the mouse and still presses and releases: the events are what make
 * the widget respond at all, and a click dispatched with no preceding move fails
 * on the vendors that require a hover state first.
 */
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
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes('Target closed') || msg.includes('Session closed')) {
        log('Warning: could not move mouse, page or session closed.');
      }
    }
  }

  async press(page: Page): Promise<void> {
    await page.mouse.down();
  }

  async release(page: Page): Promise<void> {
    await page.mouse.up();
  }

  async typeText(page: Page, field: PlaywrightElementHandle, text: string): Promise<boolean> {
    try {
      await (field as any).fill(text); // replaces, so no Control+A round is needed
      return true;
    } catch {
      /* fall through to the keyboard */
    }
    try {
      await page.keyboard.press('Control+A');
    } catch {
      /* optional */
    }
    try {
      await page.keyboard.type(text);
      return true;
    } catch (e) {
      log(`could not type into the captcha field: ${e}`);
      return false;
    }
  }
}

// ──────────────────────────────────────────────────────────────── resolution

export type HumanizationMode = 'mouse' | 'mobile' | 'none';

export const MODES: readonly HumanizationMode[] = ['mouse', 'mobile', 'none'] as const;

/** The subset of `CaptchaKrakenConfig` this module reads. */
export interface HumanizerOptions {
  humanization?: HumanizationMode;
  humanizer?: Humanizer;
  touchDriver?: any;
  touchTransform?: TouchTransform;
  startingMousePosition?: { x: number; y: number };
}

/**
 * The humanizer one solver will use, from its config.
 *
 * Precedence, and the reasoning for it:
 *
 *   1. `config.humanizer` — a caller who handed us an OBJECT has already
 *      decided; there is nothing left to select.
 *   2. `config.humanization` — an explicit mode set in code.
 *   3. `CAPTCHA_HUMANIZATION` — for a caller who cannot edit the code.
 *   4. 'mouse'.
 *
 * Note that the env var loses to code, which is the opposite of this package's
 * model-identity settings. Deliberate: which mode is right is a property of the
 * PAGE the caller is driving, and an env var flipping a desktop solve to touch
 * dispatch would break every one of them silently. Pinning a model is a
 * deployment decision; picking an input device is not.
 */
export function resolveHumanizer(config: HumanizerOptions = {}): Humanizer {
  if (config.humanizer) return config.humanizer;

  const raw = (config.humanization ?? process.env.CAPTCHA_HUMANIZATION ?? 'mouse')
    .toString()
    .trim()
    .toLowerCase() as HumanizationMode;
  if (!MODES.includes(raw)) {
    throw new Error(
      `unknown humanization mode '${raw}'; expected one of ${MODES.join(', ')}, ` +
        'or pass your own object as CaptchaKrakenConfig.humanizer',
    );
  }

  const p = config.startingMousePosition;
  const start: Point = p ? [p.x, p.y] : [0, 0];

  if (raw === 'mobile') {
    return new MobileHumanizer(start, {
      driver: config.touchDriver,
      transform: config.touchTransform,
    });
  }
  if (raw === 'none') return new NullHumanizer(start);
  return new MouseHumanizer(start);
}
