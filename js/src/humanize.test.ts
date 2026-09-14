import { strict as assert } from 'node:assert';
import test from 'node:test';

import {
  AppiumTouchBackend,
  touchBackendFor,
  MobileHumanizer,
  MouseHumanizer,
  NullHumanizer,
  PAUSE_KINDS,
  resolveHumanizer,
  type TouchBackend,
  type TouchSample,
} from './humanize.js';
import type { Page } from './playwright-types.js';

class RecordingPage {
  events: any[] = [];
  mouse = {
    move: async (x: number, y: number) => { this.events.push(['move', x, y]); },
    down: async () => { this.events.push(['down']); },
    up: async () => { this.events.push(['up']); },
  };
  keyboard = {
    type: async (t: string) => { this.events.push(['type', t]); },
    press: async (k: string) => { this.events.push(['press', k]); },
  };
  viewportSize() { return { width: 800, height: 600 }; }
  get kinds() { return this.events.map((e) => e[0]); }
  asPage() { return this as unknown as Page; }
}

class RecordingTouch implements TouchBackend {
  readonly name = 'recording';
  events: any[] = [];
  async down(x: number, y: number) { this.events.push(['down', x, y]); }
  async move(path: TouchSample[]) { this.events.push(['move', path.length]); }
  async up(x: number, y: number) { this.events.push(['up', x, y]); }
  get kinds() { return this.events.map((e) => e[0]); }
}

class RecordingDriver {
  chains: any[] = [];
  async performActions(chain: any[]) { this.chains.push(chain); }
}

const mobile = () => {
  const backend = new RecordingTouch();
  return { human: new MobileHumanizer([0, 0], { backend }), backend };
};

test('an explicit humanizer object wins over everything', () => {
  process.env.CAPTCHA_HUMANIZATION = 'mobile';
  const mine = new NullHumanizer();
  assert.equal(resolveHumanizer({ humanization: 'mouse', humanizer: mine }), mine);
  delete process.env.CAPTCHA_HUMANIZATION;
});

test('code beats the environment', () => {
  process.env.CAPTCHA_HUMANIZATION = 'mobile';
  assert.equal(resolveHumanizer({ humanization: 'none' }).name, 'none');
  delete process.env.CAPTCHA_HUMANIZATION;
});

test('the environment is read when the code says nothing', () => {
  process.env.CAPTCHA_HUMANIZATION = 'mobile';
  assert.equal(resolveHumanizer({}).name, 'mobile');
  delete process.env.CAPTCHA_HUMANIZATION;
});

test('the default is the historical behaviour', () => {
  delete process.env.CAPTCHA_HUMANIZATION;
  assert.equal(resolveHumanizer({}).name, 'mouse');
});

test('a typo names the alternatives', () => {
  delete process.env.CAPTCHA_HUMANIZATION;
  assert.throws(
    () => resolveHumanizer({ humanization: 'touch' as any }),
    /mobile[\s\S]*humanizer/,
  );
});

test('the starting position is honoured', () => {
  delete process.env.CAPTCHA_HUMANIZATION;
  const h = resolveHumanizer({ startingMousePosition: { x: 30, y: 40 } });
  assert.deepEqual(h.at, [30, 40]);
});

test('every mode answers the whole pause vocabulary without throwing', async () => {
  for (const h of [new MouseHumanizer(), new MobileHumanizer(), new NullHumanizer()]) {
    for (const kind of [...PAUSE_KINDS, 'a-kind-added-next-year' as any]) {
      await h.pause(kind);
    }
  }
});

test('a mouse click is a trajectory then a press', async () => {
  const page = new RecordingPage();
  const human = new MouseHumanizer([10, 10]);
  await human.click(page.asPage(), [400, 300]);
  assert.deepEqual(page.kinds.slice(-2), ['down', 'up']);
  assert.ok(page.kinds.filter((k) => k === 'move').length > 5);
  assert.deepEqual(human.at, [400, 300]);
});

test('the mouse mode still hovers', () => {
  assert.equal(new MouseHumanizer().hovers, true);
});

test('a move with no finger down dispatches nothing', async () => {
  const { human, backend } = mobile();
  await human.move(new RecordingPage().asPage(), [300, 200]);
  assert.deepEqual(backend.events, []);

  assert.deepEqual(human.at, [300, 200]);
});

test('a tap wobbles between touchstart and touchend', async () => {
  const { human, backend } = mobile();
  await human.click(new RecordingPage().asPage(), [120, 90]);
  assert.deepEqual(backend.kinds, ['down', 'move', 'up']);
  assert.deepEqual(backend.events[0].slice(1), [120, 90]);
});

test('a drag travels while touching', async () => {
  const { human, backend } = mobile();
  await human.drag(new RecordingPage().asPage(), [10, 10], [300, 140]);
  assert.deepEqual(backend.kinds, ['down', 'move', 'up']);
  assert.ok(backend.events[1][1] > 5);
  assert.deepEqual(human.at, [300, 140]);
});

test('mobile never reaches for the mouse', async () => {
  const page = new RecordingPage();
  const { human } = mobile();
  await human.click(page.asPage(), [50, 50]);
  await human.drag(page.asPage(), [50, 50], [200, 200]);
  assert.deepEqual(page.events, []);
});

test('mobile does not hover', () => {
  assert.equal(new MobileHumanizer().hovers, false);
});

test('reset lifts a finger a previous solve left down', async () => {
  const { human, backend } = mobile();
  const page = new RecordingPage().asPage();
  await human.press(page);
  backend.events.length = 0;
  await human.reset(page);
  assert.deepEqual(backend.kinds, ['up']);
  await human.reset(page);
  assert.deepEqual(backend.kinds, ['up']);
});

test('mobile typing clears through the element, not Control+A', async () => {
  const keys: string[] = [];
  let cleared = 0;
  const field = {
    clear: async () => { cleared++; },
    sendKeys: async (ch: string) => { keys.push(ch); },
  } as any;
  const { human } = mobile();
  assert.equal(await human.typeText(new RecordingPage().asPage(), field, 'ab7'), true);
  assert.equal(cleared, 1);
  assert.deepEqual(keys, ['a', 'b', '7']);
});

test('none spends one move per gesture and no dwell', async () => {
  const page = new RecordingPage();
  await new NullHumanizer([10, 10]).click(page.asPage(), [400, 300]);
  assert.deepEqual(page.events, [['move', 400, 300], ['down'], ['up']]);
});

test('none types with one fill', async () => {
  let value: string | null = null;
  const field = { fill: async (t: string) => { value = t; } } as any;
  assert.equal(await new NullHumanizer().typeText(new RecordingPage().asPage(), field, 'xyz'), true);
  assert.equal(value, 'xyz');
});

test('the Appium chain is a W3C touch pointer', async () => {
  const driver = new RecordingDriver();
  await new AppiumTouchBackend(driver).down(12, 34);
  const [pointer] = driver.chains[0];
  assert.equal(pointer.type, 'pointer');
  assert.deepEqual(pointer.parameters, { pointerType: 'touch' });
  assert.deepEqual(pointer.actions.map((a: any) => a.type), ['pointerMove', 'pointerDown']);
  assert.deepEqual([pointer.actions[0].x, pointer.actions[0].y], [12, 34]);
  assert.equal(pointer.actions[0].origin, 'viewport');
});

test('an Appium leg is one chain with per-sample durations', async () => {
  const driver = new RecordingDriver();
  await new AppiumTouchBackend(driver).move([[1, 2, 11], [3, 4, 12.6]]);
  assert.equal(driver.chains.length, 1);
  assert.deepEqual(driver.chains[0][0].actions.map((a: any) => a.duration), [11, 13]);
});

test('Appium press and release are separate performs', async () => {
  const driver = new RecordingDriver();
  const backend = new AppiumTouchBackend(driver);
  await backend.down(0, 0);
  await backend.move([[5, 5, 8]]);
  await backend.up(5, 5);
  assert.equal(driver.chains.length, 3);
  assert.deepEqual(driver.chains[2][0].actions, [{ type: 'pointerUp', button: 0 }]);
});

test('CSS pixels are mapped onto the device', async () => {
  const driver = new RecordingDriver();
  await new AppiumTouchBackend(driver, { scale: 3, origin: [0, 132] }).down(10, 20);
  const move = driver.chains[0][0].actions[0];
  assert.deepEqual([move.x, move.y], [30, 192]);
});

test('a driver that speaks neither call says so', async () => {
  await assert.rejects(
    () => new AppiumTouchBackend({}).down(0, 0),
    /performActions[\s\S]*execute/,
  );
});

class RatioPage {
  reads = 0;
  constructor(private dpr: number | null) {}
  async evaluate(expression: string) {
    this.reads += 1;
    if (this.dpr === null) throw new Error('no execution context');
    return this.dpr;
  }
}

test('a hidpi session with no transform refuses', async () => {
  const driver = new RecordingDriver();
  const backend = new AppiumTouchBackend(driver, {}, new RatioPage(3) as any);
  await assert.rejects(() => backend.down(10, 20), /devicePixelRatio 3[\s\S]*scale/);
  assert.equal(driver.chains.length, 0, 'refused, not dispatched');
});

test('the refusal names the half it cannot measure', async () => {
  const backend = new AppiumTouchBackend(new RecordingDriver(), {}, new RatioPage(2.625) as any);
  await assert.rejects(() => backend.down(0, 0), /origin/);
});

test("an explicit scale is taken as the caller's word", async () => {
  const driver = new RecordingDriver();
  await new AppiumTouchBackend(driver, { scale: 1 }, new RatioPage(3) as any).down(10, 20);
  const move = driver.chains[0][0].actions[0];
  assert.deepEqual([move.x, move.y], [10, 20]);
});

test('a 1x session needs no transform', async () => {
  const driver = new RecordingDriver();
  await new AppiumTouchBackend(driver, {}, new RatioPage(1) as any).down(10, 20);
  assert.equal(driver.chains.length, 1);
});

test('a page that cannot be asked is not refused', async () => {
  const driver = new RecordingDriver();
  await new AppiumTouchBackend(driver, {}, new RatioPage(null) as any).down(10, 20);
  assert.equal(driver.chains.length, 1);
});

test('no page at all is not refused', async () => {
  const driver = new RecordingDriver();
  await new AppiumTouchBackend(driver).down(10, 20);
  assert.equal(driver.chains.length, 1);
});

test('the ratio is read once, not per gesture', async () => {
  const page = new RatioPage(1);
  const backend = new AppiumTouchBackend(new RecordingDriver(), {}, page as any);
  await backend.down(1, 1);
  await backend.move([[2, 2, 5]]);
  await backend.up(2, 2);
  assert.equal(page.reads, 1);
});

test('the factory hands the backend its page', async () => {
  const backend = await touchBackendFor(new RatioPage(3) as any, new RecordingDriver());
  await assert.rejects(() => backend.down(0, 0), /devicePixelRatio/);
});



