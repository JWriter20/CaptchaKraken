# 🧑‍💻 Usage

Install a port, hand the solver a browser page (or a screenshot), and it does
detect → grid → click → verify.

> **Set up the model first.** Everything below assumes the solver knows where to
> send inference requests: [Hosted API](./hosted-api.md) (no GPU, ~1 minute) or
> [Self-hosting](./self-hosting.md) (your own card).

## Install

Two ports, pick your language. The **TypeScript** port (npm) is the browser
driver; the **Python** port (PyPI) is the engine + CLI (also usable standalone
to solve a screenshot). Both talk to the same vLLM server and are model-agnostic.

```bash
npm install captchakraken          # TypeScript browser driver
# or
pip install captchakraken          # Python engine + `captchakraken` CLI
```

Python one-liner (solve a screenshot → JSON click plan):

```bash
captchakraken path/to/captcha.png      # local server auto-starts on first call
```

## Two drivers: TypeScript and Python

Since **2.3.0** the solver drives a browser from either language, and both are
first-class:

| | import | notes |
|---|---|---|
| TypeScript | `import { CaptchaKrakenSolver } from 'captchakraken'` | async; spawns the Python engine as a subprocess |
| Python | `from captchakraken import PageSolver` | **sync only**; calls the engine in-process |

They are mirrors of each other, deliberately. The split is identical on both
sides: **vision, CV and prompting live in the shared Python half**
(`solver.py`, `planner.py`, `tool_calls/`) and the driver only finds the
challenge and clicks. The TypeScript driver reaches that half by spawning the
CLI and talking JSON over a pipe; the Python driver calls the very same
functions in-process — no subprocess, no CV worker. That is the *only* intended
difference, so the two cannot drift on anything that decides accuracy.

```python
from playwright.sync_api import sync_playwright
from captchakraken import PageSolver

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto("https://example.com/protected")

    result = PageSolver().solve(page)
    print(result.is_solved, result.token_usage)
```

Errors are typed, because they mean different things about the page:
`NoCaptchaFoundError` (reCAPTCHA v3 / invisible — nothing to solve),
`UnsupportedChallengeError` (a settled frame of a kind we don't handle, e.g. an
hCaptcha click/drag puzzle), `AnimatedChallengeError` (an animated challenge we
could not RECORD — note this no longer means "the challenge moves"; a moving
challenge is recorded and solved from keyframes), and `CaptchaSolveError` for
everything else.

Tune with `PageSolverConfig`; its fields are the snake_cased names of the
TypeScript `CaptchaKrakenConfig` keys, so a value tuned on one driver is findable
on the other.

> **The Python driver is synchronous only.** A sync Playwright handle cannot be
> driven from inside an event loop, so `async_playwright` and `AsyncCamoufox`
> are not supported by it — use the TypeScript driver for async work until the
> async mirror lands.

### camoufox (Python)

```bash
pip install "camoufox[captcha]"
```

```python
from camoufox.sync_api import Camoufox
from camoufox.captcha import solve_captcha

with Camoufox(headless=False) as browser:
    page = browser.new_page()
    page.goto("https://example.com/protected")
    solve_captcha(page)
```

---

## Bring your own browser

Neither driver launches the browser for you — you **bring your
own** browser and hand the solver a page. Install whichever automation framework
you prefer alongside it. The solver itself ships **no browser dependency**.

| Framework | Install | Pass to `solve()` |
|---|---|---|
| [Playwright](https://www.npmjs.com/package/playwright) (vanilla) | `npm i playwright` | the `page` directly |
| [Patchright](https://www.npmjs.com/package/patchright) (stealth Chromium) | `npm i patchright` | the `page` directly |
| [camoufox-js](https://www.npmjs.com/package/camoufox-js) (Firefox stealth) | `npm i camoufox-js` | the `page` directly |
| [Puppeteer](https://www.npmjs.com/package/puppeteer) | `npm i puppeteer` | `fromPuppeteer(page)` |

The first three are Playwright-compatible (they return a standard Playwright
`Page`), so you pass the page straight in. Puppeteer's API differs slightly, so
wrap its page once with `fromPuppeteer()`. All four are tested end-to-end against
the live reCAPTCHA demo.

In every example the solver reads `VLLM_BASE_URL` + `CAPTCHA_KRAKEN_API_KEY`
from the environment, or from `~/.captchakraken/credentials` if they are unset.
Set them up either way first — [Hosted API](./hosted-api.md) (no GPU) or
[Self-hosting](./self-hosting.md#configuration) (`setup.sh`, then
`source captchakraken.env`). Then `solve()` does detect → grid → click → verify.

### Playwright (vanilla)

```typescript
import { chromium } from 'playwright';
import { CaptchaKrakenSolver } from 'captchakraken';

const browser = await chromium.launch({ headless: false });
const page = await (await browser.newContext()).newPage();
await page.goto('https://www.google.com/recaptcha/api2/demo');

const solver = new CaptchaKrakenSolver();
await solver.solve(page);

await browser.close();
```

### Patchright (stealth-patched Chromium)

Drop-in for vanilla Playwright — same API, just a stealthier Chromium.

```typescript
import { chromium } from 'patchright';
import { CaptchaKrakenSolver } from 'captchakraken';

const browser = await chromium.launch({ headless: false });
const page = await (await browser.newContext()).newPage();
await page.goto('https://www.google.com/recaptcha/api2/demo');

const solver = new CaptchaKrakenSolver();
await solver.solve(page);

await browser.close();
```

### camoufox-js (Firefox stealth)

[`camoufox-js`](https://github.com/apify/camoufox-js) exposes a `Camoufox()`
launcher that returns a standard Playwright Browser. (On first run it may prompt
you to fetch its Firefox build: `npx camoufox-js fetch`.)

```typescript
import { Camoufox } from 'camoufox-js';
import { CaptchaKrakenSolver } from 'captchakraken';

const browser = await Camoufox({ headless: false });
const page = await (await browser.newContext()).newPage();
await page.goto('https://www.google.com/recaptcha/api2/demo');

const solver = new CaptchaKrakenSolver();
await solver.solve(page);

await browser.close();
```

### Puppeteer (via `fromPuppeteer`)

Puppeteer isn't Playwright-API-compatible, so wrap its page with the bundled
`fromPuppeteer()` adapter. Drive the **raw** Puppeteer page as usual (navigate,
etc.); only the object you hand `solve()` needs wrapping.

```typescript
import puppeteer from 'puppeteer';
import { CaptchaKrakenSolver, fromPuppeteer } from 'captchakraken';

const browser = await puppeteer.launch({ headless: false });
const page = await browser.newPage();
await page.goto('https://www.google.com/recaptcha/api2/demo');

const solver = new CaptchaKrakenSolver();
await solver.solve(fromPuppeteer(page));

await browser.close();
```

That's it — no model name to pass, no provider to choose, and **no browser locked
in**. The solver defaults to the unified captcha LoRA and the endpoint from your
env, and works with whatever browser you handed it.

### Puppeteer in Python — there isn't one

`fromPuppeteer()` is TypeScript-only, because Puppeteer is a Node library.
Python's `pyppeteer` last released in February 2024, pins `urllib3 <2.0.0`
against CaptchaKraken's own `requests`, and is async-only against a synchronous
driver — two independent blockers. Playwright is the Python path, and
`patchright` / `camoufox` are stealth-patched builds of it.

## Solve captchas as they appear

`solve(page)` is a one-shot: it solves whatever challenge is on the page right
now. When you do not know *where* in a script a captcha will show up, install a
watcher instead and let it handle them underneath your automation.

**TypeScript** — returns immediately, watches in the background:

```typescript
const solver = new CaptchaKrakenSolver();
const watcher = solver.watch(page, {
  onSolved: (r) => console.log('solved:', r.isSolved),
});

await page.goto('https://example.com/protected');   // solved as it appears
// ... the rest of your automation ...

await watcher.stop();                               // waits for a solve in flight
```

**Python** — the same idea, in the two shapes a synchronous driver allows:

```python
solver = PageSolver()

solver.watch(page).run()          # blocking: hold this page clean

watcher = solver.watch(page)      # or cooperatively, inside your own loop
while working():
    watcher.poll_once()
```

Options are the same on both (snake_cased in Python): `interval_ms` (default
1000), `max_solves`, `error_backoff_ms` (default 5000, applied only after a
solve *raises*), `on_solved`, `on_error`.

### Why Python blocks and TypeScript does not

A synchronous Playwright handle is bound to the greenlet that created it, so a
worker thread cannot drive the page — `threading` would buy an exception, not a
background watcher. The TypeScript driver is async and has no such limit. This
is the one place the two ports deliberately differ.

### It injects nothing into the page

The obvious implementation is a `MutationObserver` in the page that calls out
through an exposed binding. That reacts faster, and it is the one design that
cannot be made stealthy everywhere: an exposed binding puts a function on
`window`, and an observer is script the page can enumerate.

So the watcher injects nothing at all. It drives the same `detectCaptcha()` the
solver already uses, from the driver side, on a timer — there is no new
detection surface on any launcher. Under camoufox the DOM reads that probe
performs run in the sandboxed Juggler world, because that is camoufox's default
for **all** Playwright evaluation (`main_world_eval` and an `mw:` prefix are the
opt-*out*). Nothing here opts out, so the page can no more see the watcher than
it can see Playwright itself.

The trade is latency: a captcha that appears just after a tick waits up to one
`interval_ms` to be noticed. Against a solve measured in seconds, that is not
the number that matters.

## Cloning this repo

If you're cloning instead of installing from npm, initialize the submodule that
holds the detection/planner CLI:

```bash
git submodule update --init --recursive
npm install        # builds the solver + a local CLI venv (postinstall)
```

## Migrating from v1

> **⚠️ v2 is a breaking change.** It's a full rewrite. The old multi-provider
> setup (Gemini / OpenRouter / Ollama) is gone — v2 runs one purpose-built grid
> model on a local **vLLM** server.

- The `apiProvider` / `model` / `apiKey` options for Gemini/OpenRouter/Ollama are
  **removed**. v2 talks only to a vLLM server.
- Set **`VLLM_BASE_URL`** and **`CAPTCHA_KRAKEN_API_KEY`** (or run `setup.sh`,
  or point them at the [hosted API](./hosted-api.md)) instead of provider keys.
- `new CaptchaKrakenSolver()` now needs no model/provider — it defaults to the
  grid LoRA.
- v1's `transformers` / `torch` / SAM3 dependencies are gone from the solver venv.

---

← Back to [docs index](./README.md)
