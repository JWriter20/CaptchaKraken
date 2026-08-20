/**
 * The Puppeteer adapter's translation layer, pinned delta by delta.
 *
 * This file exists because the adapter's own header claimed it was "verified
 * against Puppeteer 24.x" while nothing verified it: the package had no test
 * that touched it, and Tier 3 drives Camoufox only, on both ports. Every
 * assertion below is one of the API differences that header lists, so a
 * Puppeteer release that moves one of them fails here instead of failing as a
 * mysteriously unsolvable captcha in a user's automation.
 *
 * A recording fake rather than a real browser: what is being tested is the
 * TRANSLATION — that Playwright-shaped calls arrive at Puppeteer-shaped ones —
 * and that is a property of the wrapper, observable without launching Chrome.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { fromPuppeteer } from './puppeteer-adapter';

interface Call { method: string; args: any[]; }

// Not `calls.at(-1)`: the package targets ES2020, where Array.prototype.at
// does not exist. Tests compile under the same target as the shipped code.
const last = (calls: Call[]): Call => calls[calls.length - 1];

function fakeHandle(calls: Call[], name = 'handle') {
  const handle: any = {
    screenshot: async (o: any) => { calls.push({ method: `${name}.screenshot`, args: [o] }); return Buffer.from(''); },
    contentFrame: async () => { calls.push({ method: `${name}.contentFrame`, args: [] }); return fakeFrame(calls); },
    boundingBox: async () => { calls.push({ method: `${name}.boundingBox`, args: [] }); return { x: 1, y: 2, width: 3, height: 4 }; },
    scrollIntoView: async () => { calls.push({ method: `${name}.scrollIntoView`, args: [] }); },
    isVisible: async () => { calls.push({ method: `${name}.isVisible`, args: [] }); return true; },
    evaluate: async (fn: Function, ...args: any[]) => {
      calls.push({ method: `${name}.evaluate`, args });
      // Run against a stub element so the wrapper's own closure is exercised.
      return fn({ getAttribute: (n: string) => `attr:${n}`, textContent: 'text!' }, ...args);
    },
    $: async (sel: string) => { calls.push({ method: `${name}.$`, args: [sel] }); return fakeHandle(calls, 'child'); },
  };
  return handle;
}

function fakeFrame(calls: Call[]) {
  return {
    $: async (sel: string) => { calls.push({ method: 'frame.$', args: [sel] }); return fakeHandle(calls); },
    waitForSelector: async (sel: string, o: any) => { calls.push({ method: 'frame.waitForSelector', args: [sel, o] }); return fakeHandle(calls); },
    waitForFunction: async (fn: any, ...rest: any[]) => { calls.push({ method: 'frame.waitForFunction', args: [fn, ...rest] }); return true; },
  };
}

function fakePage(calls: Call[], over: Record<string, any> = {}) {
  return {
    mouse: {
      move: async (x: number, y: number, o: any) => { calls.push({ method: 'mouse.move', args: [x, y, o] }); },
      down: async (o: any) => { calls.push({ method: 'mouse.down', args: [o] }); },
      up: async (o: any) => { calls.push({ method: 'mouse.up', args: [o] }); },
    },
    keyboard: {
      type: async (t: string, o: any) => { calls.push({ method: 'keyboard.type', args: [t, o] }); },
      press: async (k: string, o: any) => { calls.push({ method: 'keyboard.press', args: [k, o] }); },
      down: async (k: string, o: any) => { calls.push({ method: 'keyboard.down', args: [k, o] }); },
      up: async (k: string) => { calls.push({ method: 'keyboard.up', args: [k] }); },
    },
    waitForSelector: async (sel: string, o: any) => { calls.push({ method: 'waitForSelector', args: [sel, o] }); return fakeHandle(calls); },
    viewport: () => { calls.push({ method: 'viewport', args: [] }); return { width: 1280, height: 720 }; },
    $: async (sel: string) => { calls.push({ method: '$', args: [sel] }); return fakeHandle(calls); },
    $$: async (sel: string) => { calls.push({ method: '$$', args: [sel] }); return [fakeHandle(calls), fakeHandle(calls)]; },
    $eval: async (sel: string, fn: any, arg: any) => { calls.push({ method: '$eval', args: [sel, arg] }); return 'evaled'; },
    isClosed: () => false,
    ...over,
  } as any;
}

test('viewportSize() reads Puppeteer viewport()', () => {
  const calls: Call[] = [];
  const page = fromPuppeteer(fakePage(calls));
  assert.deepEqual(page.viewportSize(), { width: 1280, height: 720 });
  assert.equal(calls[0].method, 'viewport', 'called Playwright viewportSize() on Puppeteer');
});

test('waitForTimeout resolves on a timer — Puppeteer removed the method', async () => {
  const page = fromPuppeteer(fakePage([]));
  const start = Date.now();
  await page.waitForTimeout(30);
  assert.ok(Date.now() - start >= 25, 'returned early; a solver wait would be skipped');
});

test('{state:visible|hidden} becomes Puppeteer {visible|hidden}', async () => {
  const calls: Call[] = [];
  const page = fromPuppeteer(fakePage(calls));

  await page.waitForSelector('#a', { state: 'visible', timeout: 500 });
  assert.deepEqual(last(calls).args[1], { timeout: 500, visible: true });

  await page.waitForSelector('#b', { state: 'hidden' });
  assert.deepEqual(last(calls).args[1], { hidden: true });
});

test("{state:'attached'} passes no visibility flag", async () => {
  const calls: Call[] = [];
  const page = fromPuppeteer(fakePage(calls));
  await page.waitForSelector('#c', { state: 'attached', timeout: 10 });
  const opts = last(calls).args[1];
  assert.deepEqual(opts, { timeout: 10 });
  assert.ok(!('visible' in opts) && !('hidden' in opts), 'attached must not imply visible');
});

test('no options stays undefined rather than becoming {}', async () => {
  const calls: Call[] = [];
  const page = fromPuppeteer(fakePage(calls));
  await page.waitForSelector('#d');
  assert.equal(last(calls).args[1], undefined);
});

test('getAttribute and textContent go through evaluate()', async () => {
  const calls: Call[] = [];
  const page = fromPuppeteer(fakePage(calls));
  const handle = (await page.$('#x'))!;

  assert.equal(await handle.getAttribute('src'), 'attr:src');
  assert.equal(last(calls).method, 'handle.evaluate');
  assert.deepEqual(last(calls).args, ['src'], 'attribute name must reach the page function');

  assert.equal(await handle.textContent(), 'text!');
  assert.equal(last(calls).method, 'handle.evaluate');
});

test('scrollIntoViewIfNeeded maps to scrollIntoView', async () => {
  const calls: Call[] = [];
  const page = fromPuppeteer(fakePage(calls));
  await (await page.$('#x'))!.scrollIntoViewIfNeeded();
  assert.equal(last(calls).method, 'handle.scrollIntoView');
});

test("a 'Control+A' combo is held, pressed, released — Puppeteer has no combo syntax", async () => {
  const calls: Call[] = [];
  const page = fromPuppeteer(fakePage(calls));
  await page.keyboard.press('Control+A');

  assert.deepEqual(
    calls.map((c) => `${c.method}:${c.args[0]}`),
    ['keyboard.down:Control', 'keyboard.press:A', 'keyboard.up:Control'],
    'a bare press("Control+A") types a literal string in Puppeteer and never selects anything',
  );
});

test('a plain key is pressed once, with no modifier traffic', async () => {
  const calls: Call[] = [];
  const page = fromPuppeteer(fakePage(calls));
  await page.keyboard.press('Enter');
  assert.deepEqual(calls.map((c) => c.method), ['keyboard.press']);
});

test('waitForFunction swaps Playwright (fn, arg, opts) to Puppeteer (fn, opts, arg)', async () => {
  const calls: Call[] = [];
  const page = fromPuppeteer(fakePage(calls));
  const frame = await (await page.$('#f'))!.contentFrame();

  const fn = () => true;
  await frame!.waitForFunction(fn, { some: 'arg' }, { timeout: 99 });

  const call = calls.find((c) => c.method === 'frame.waitForFunction')!;
  assert.equal(call.args[0], fn);
  assert.deepEqual(call.args[1], { timeout: 99 }, 'options must be the SECOND positional arg for Puppeteer');
  assert.deepEqual(call.args[2], { some: 'arg' }, 'the arg must come last');
});

test('$$ wraps every handle it returns', async () => {
  const calls: Call[] = [];
  const page = fromPuppeteer(fakePage(calls));
  const handles = await page.$$('iframe');
  assert.equal(handles.length, 2);
  for (const h of handles) {
    assert.equal(typeof h.getAttribute, 'function', 'an unwrapped Puppeteer handle would have no getAttribute');
  }
});

test('a missing element stays null instead of becoming a broken wrapper', async () => {
  const calls: Call[] = [];
  const page = fromPuppeteer(fakePage(calls, { $: async () => null, waitForSelector: async () => null }));
  assert.equal(await page.$('#nope'), null);
  assert.equal(await page.waitForSelector('#nope'), null);
});

test('isClosed is forwarded — the watcher needs it to end its loop', async () => {
  let closed = false;
  const page = fromPuppeteer(fakePage([], { isClosed: () => closed }));
  assert.equal(page.isClosed!(), false);
  closed = true;
  assert.equal(page.isClosed!(), true, 'a watcher would poll a dead page forever');
});
