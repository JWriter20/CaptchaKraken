/**
 * The compatibility claims, checked against REAL browser libraries.
 *
 * puppeteer-adapter.test.ts pins the translation with a fake, which cannot
 * catch the failure that actually matters here: Puppeteer or Playwright
 * CHANGING one of the methods we call. `viewport()` going away, `isVisible()`
 * moving, `waitForFunction`'s argument order flipping — a fake happily keeps
 * agreeing with a wrapper that no longer matches the library.
 *
 * So this file launches the real thing and drives every member of the
 * structural `PlaywrightPage` through it:
 *
 *   - vanilla Playwright, passed to the solver surface with NO adapter, which
 *     is the "any Playwright-compatible launcher works" claim in index.ts;
 *   - Puppeteer through `fromPuppeteer`, which is the "verified against
 *     Puppeteer 24.x" claim the adapter header makes.
 *
 * SKIPPED WHEN THE LIBRARY IS ABSENT, and deliberately not a devDependency:
 * this package ships with ZERO browser dependencies, and making one a dev
 * dependency would put a ~200MB browser download in front of every contributor
 * who only wanted to run the unit tests. To run these:
 *
 *     npm i --no-save puppeteer playwright && npx playwright install chromium
 *     npm test
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { fromPuppeteer } from './puppeteer-adapter';
import { watchPage } from './watcher';
import { PlaywrightPage } from './playwright-types';

/** Resolve an optional browser library, or null when it is not installed. */
function optional(name: string): any | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    return require(name);
  } catch {
    return null;
  }
}

const puppeteer = optional('puppeteer');
const playwright = optional('playwright');
const LAUNCH_ARGS = ['--no-sandbox', '--disable-dev-shm-usage'];

const HTML =
  '<body style="height:3000px">' +
  '<div id="target" data-vendor="recaptcha">hello captcha</div>' +
  '<div id="hidden" style="display:none">nope</div>' +
  '<input id="field" />' +
  '<iframe id="frame" srcdoc="<div id=\'inner\'>inner text</div>"></iframe>' +
  '</body>';

/**
 * Exercise every member of the structural page surface.
 *
 * One body for both libraries: that IS the claim under test — the solver only
 * ever calls these, so if they all work on a page, the solver works on it.
 */
async function exerciseSurface(page: PlaywrightPage): Promise<void> {
  assert.deepEqual(page.viewportSize(), { width: 1280, height: 720 }, 'viewportSize');

  const target = await page.$('#target');
  assert.ok(target, '$ returned nothing');
  assert.equal(await target!.getAttribute('data-vendor'), 'recaptcha', 'getAttribute');
  assert.equal((await target!.textContent())?.trim(), 'hello captcha', 'textContent');
  assert.equal(await target!.isVisible(), true, 'isVisible (visible element)');
  assert.ok((await target!.boundingBox())!.width > 0, 'boundingBox');
  await target!.scrollIntoViewIfNeeded();
  assert.ok((await target!.screenshot()).length > 0, 'element screenshot');

  const hidden = await page.$('#hidden');
  assert.equal(await hidden!.isVisible(), false, 'isVisible (display:none)');

  assert.ok((await page.$$('div')).length >= 2, '$$');
  assert.ok(await (await page.$('body'))!.$('#target'), 'nested handle.$');
  assert.ok(await page.waitForSelector('#target', { state: 'visible', timeout: 5000 }), 'waitForSelector {state:visible}');
  assert.equal(await page.$eval('#target', (el) => el.id), 'target', '$eval');

  const started = Date.now();
  await page.waitForTimeout(50);
  assert.ok(Date.now() - started >= 45, 'waitForTimeout returned early');

  // The iframe path, which is how every real captcha is reached.
  const frame = await (await page.$('#frame'))!.contentFrame();
  assert.ok(frame, 'contentFrame');
  assert.ok(await frame!.$('#inner'), 'frame.$');
  assert.ok(await frame!.waitForSelector('#inner', { state: 'visible', timeout: 5000 }), 'frame.waitForSelector');
  // Argument ORDER is the delta the adapter bridges: Playwright takes
  // (fn, arg, options) and Puppeteer takes (fn, options, ...args).
  await frame!.waitForFunction((sel: any) => !!document.querySelector(sel), '#inner', { timeout: 5000 });

  await page.mouse.move(100, 100, { steps: 4 });
  await page.mouse.down({ button: 'left' });
  await page.mouse.up({ button: 'left' });

  await page.keyboard.type('abc', { delay: 1 });
  await page.keyboard.press('Control+A');   // combo: one call in PW, three in Puppeteer
  await page.keyboard.press('Backspace');

  assert.equal(page.isClosed!(), false, 'isClosed on an open page');
}

test('vanilla Playwright satisfies the solver surface with no adapter', { skip: !playwright && 'playwright not installed' }, async () => {
  const browser = await playwright.chromium.launch({ headless: true, args: LAUNCH_ARGS });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    const page = await context.newPage();
    await page.setContent(HTML);
    await page.focus('#field');
    await exerciseSurface(page as unknown as PlaywrightPage);
  } finally {
    await browser.close();
  }
});

test('Puppeteer satisfies it through fromPuppeteer()', { skip: !puppeteer && 'puppeteer not installed' }, async () => {
  const browser = await puppeteer.launch({ headless: true, args: LAUNCH_ARGS });
  try {
    const raw = await browser.newPage();
    await raw.setViewport({ width: 1280, height: 720 });
    await raw.setContent(HTML);
    await raw.focus('#field');

    const page = fromPuppeteer(raw);
    await exerciseSurface(page);

    await browser.close();
    assert.equal(page.isClosed!(), true, 'isClosed did not follow the real page shut');
  } finally {
    if (browser.connected) await browser.close();
  }
});

test('the watcher solves a captcha that appears after it is installed', { skip: !playwright && 'playwright not installed' }, async () => {
  const browser = await playwright.chromium.launch({ headless: true, args: LAUNCH_ARGS });
  try {
    const page = await browser.newPage();
    await page.setContent('<body></body>');

    let solves = 0;
    const solver = {
      async detectCaptcha(p: any) { return await p.$('#late-captcha'); },
      async solve(p: any) {
        // Removing it is what a real solve does to the challenge: the next
        // probe must then find nothing, or the watcher re-solves forever.
        await p.$eval('#late-captcha', (el: any) => el.remove());
        solves += 1;
        return { isSolved: true } as any;
      },
    };

    const watcher = watchPage(solver, page as unknown as PlaywrightPage, { intervalMs: 25 });
    await page.evaluate(() => {
      setTimeout(() => {
        const el = document.createElement('div');
        el.id = 'late-captcha';
        document.body.appendChild(el);
      }, 100);
    });

    await new Promise((r) => setTimeout(r, 700));
    await watcher.stop();

    assert.equal(solves, 1, 'the captcha was solved ' + solves + ' times, expected exactly once');
    assert.equal(watcher.running, false);
  } finally {
    await browser.close();
  }
});

test('one watcher covers every navigation on the page', { skip: !playwright && 'playwright not installed' }, async () => {
  /**
   * The claim the whole per-page design rests on.
   *
   * `watch(page)` is installed ONCE and is expected to keep working as the page
   * navigates — which is what makes a browser-wide installer unnecessary for the
   * case people actually hit: a challenge appearing on request 40 of a scrape,
   * on the same `Page` object the run started with. If a watcher stopped at the
   * first navigation, every user would need to re-install after each `goto` and
   * the API would be the wrong shape.
   *
   * Waits on the COUNT rather than sleeping a fixed span: `node --test` runs
   * test files in parallel, so several Chromium launches compete for CPU and a
   * fixed 400ms is enough alone and not enough in the suite. That is how the
   * first version of this test passed in isolation and flaked in CI.
   */
  const browser = await playwright.chromium.launch({ headless: true, args: LAUNCH_ARGS });
  try {
    const page = await browser.newPage();
    let solves = 0;
    const solver = {
      async detectCaptcha(p: any) { return await p.$('#c'); },
      async solve(p: any) {
        await p.$eval('#c', (el: any) => el.remove());
        solves += 1;
        return { isSolved: true } as any;
      },
    };

    const until = async (want: number, why: string) => {
      const deadline = Date.now() + 15_000;
      while (solves < want && Date.now() < deadline) await new Promise((r) => setTimeout(r, 25));
      assert.equal(solves, want, why);
    };

    const watcher = watchPage(solver, page as unknown as PlaywrightPage, { intervalMs: 25 });

    // Two clean navigations: the watcher must stay quiet, not error out. This
    // one IS a fixed settle, because it asserts an absence — there is no count
    // to wait for.
    await page.goto('data:text/html,<body>one</body>');
    await page.goto('data:text/html,<body>two</body>');
    await new Promise((r) => setTimeout(r, 500));
    assert.equal(solves, 0, 'solved something on a page with no captcha');

    await page.goto('data:text/html,<body><div id="c"></div>three</body>');
    await until(1, 'the watcher did not survive navigation');

    // Again, to prove it is still armed rather than having fired once.
    await page.goto('data:text/html,<body><div id="c"></div>four</body>');
    await until(2, 'the watcher stopped arming after its first solve');

    await watcher.stop();
  } finally {
    await browser.close();
  }
});
