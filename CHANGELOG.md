# Changelog

All notable changes to CaptchaKraken are documented here. This project follows
semantic versioning; v2 is a major, **breaking** release.

## [Unreleased]

### Added
- **Distorted-text captchas are typed, and puzzle-piece sliders are dragged.**
  Two answer families the model was already trained to produce and the driver
  silently threw away. `ActionPlanner._normalize_pixel` dropped both — a
  `{"action": "type", "text": …}` answer has no coordinate for any branch to key
  off, and the drag branch required BOTH ends, so the sourceless drag the
  "FOR PUZZLE PIECE SLIDER PUZZLES" clause explicitly asks for parsed to
  nothing. A perfectly correct answer became "unsupported".

  **Text.** The driver decides from the DOM, not the picture: a visible text box
  in the challenge selects the distorted-text prompt (`--text-mode` on the CLI)
  and skips grid detection, because BotDetect's boxed glyphs are exactly the
  lattice `find_grid` looks for. The answer is typed character by character with
  jittered gaps, after a real pointer move and click — `fill()` would set the
  value with no keystrokes at all, and these are the vendors that score cadence.
  A retry clears the box first, so round 2 cannot append to round 1.

  **Sliders.** Closed-loop, not a calculation. The model gives the centre of the
  gap — all the picture can tell it — but not how far the handle must travel to
  put the piece there, which is a ratio several vendors deliberately vary. So the
  driver presses the handle, nudges it twice by known amounts, and watches:
  `union(before, after)` spans the piece's original left edge to its current
  right edge, so its width is `piece_width + ratio x nudge`. Two nudges, two
  unknowns, solved; then it steers the remainder and re-measures. The mouse is
  not released until the piece is home, because on every one of these puzzles
  **releasing is the submit** — which is also why a completed slide is never
  followed by a Verify click.

  New surface: `TypeAction` (JS), a nullable `DragAction.source_bounding_box`
  (null = slider), `TEXT_INPUT_SELECTORS` / `SLIDER_HANDLE_SELECTORS` /
  `DRAGGABLE_PIECE_SELECTORS` (vendor-first, generic-last — the generic tail is
  what fires on most real pages), the `slide_*` knobs on `PageSolverConfig`,
  `tool_calls/track_piece.py`, and CLI `track-piece` (also a `serve` cmd, since
  it runs several times per drag with the button held). `prompts.py` gained the
  generation-2 `text` family, which the client had been missing since the
  finetune repo defined it.

  **Requires a generation-2 model.** Generation 1 — including the currently
  served `CaptchaKraken_v1.1` — has no text prompt and no slider clause, so a v1
  model is never asked for either answer. Text captchas now report
  `UnsupportedCaptchaError` naming that reason rather than clicking at random.
- **Animated challenges are solved instead of skipped.** hCaptcha's "select the
  odd animal" (sprites cross-fading on independent cycles) and "unique motion
  pattern" (identical meshes, only the rotation differs) carry none of their
  answer in any single frame. The driver now records the widget for 4 s at 10 fps,
  reduces the recording to the few stills that carry the answer, and sends those
  as **one multi-image request**.

  The answer gains a `"frame"` naming which still it acted on, and the driver
  **holds the mouse until the widget looks like that frame again** before
  clicking — comparing only the neighbourhood of the click point, because
  everything else on these puzzles is moving too. Without that wait, a click on a
  fading sprite lands on background.

  New surface: `CaptchaSolver.solve_keyframes()`,
  `ActionPlanner.get_keyframe_actions()`, `keyframes.py` (a verbatim copy of the
  training repo's slicer — the two are checked byte-for-byte in CI, because the
  model answers with a frame NUMBER and a solver that sliced differently would
  wait for a picture that does not exist). Actions carry `await_keyframe` +
  `frame`. CLI: `solve-animated --frames-dir DIR` and `match-region` (also a
  `serve` cmd, since the wait gate polls it every ~120 ms).

  Config: `video_solve_enabled` / `videoSolveEnabled` (default on),
  `video_burst_duration_ms` / `video_burst_fps`, `keyframe_wait_timeout_ms` /
  `keyframe_wait_poll_ms`, and the camelCase equivalents on the TS side.

  **The clip is never sent to the model.** Every mp4 this project can write is
  MPEG-4 Part 2, whose decodability on the serving side was never verified, and a
  clip cannot carry a frame number in the first place. Frames stay in memory and
  are sliced there, so no encode happens on the solve path at all.

  Accuracy depends on the adapter: the pipeline is in place, and an adapter
  trained on the keyframe format is what makes the answers good.

### Changed
- **`AnimatedChallengeError` / `.animated` narrowed.** It used to mean "the
  challenge never settles, give up". It now means "an animated challenge we could
  not RECORD" — the element refused to screenshot, or `video_solve_enabled` is
  off. A moving challenge is no longer a failure.
- The Python driver now runs the settle probe for `puzzle_source == "unknown"`
  (GeeTest, Tencent, …) as well as hCaptcha. It never did, so an animated
  non-hCaptcha widget was screenshotted mid-cycle and answered from whatever
  single moment happened to be caught. reCAPTCHA is deliberately excluded: it has
  its own readiness gate and its grids are never animated.
- `"unsupported"` mid-solve followed by a never-settling next round used to be
  terminal. It now retries into the recording path, still bounded by
  `max_unsupported_resolves`.
- The frame-freshness guard is skipped for animated challenges. It re-solves when
  the frame changes during inference, and these change by definition — every
  attempt would be judged stale and the whole re-solve budget would burn without
  ever acting. The `frame` in the answer is the guard that replaces it.
- `CaptchaSolver.solveVideo()` still aliases `solve()` (one still, one answer) and
  is NOT redirected to `solve_keyframes()`: callers of that name pass a single
  media path, and reinterpreting it as "record and slice" would change what an
  existing integration does. New code calls `solve_keyframes()` explicitly.

## [2.4.0] — 2026-07-29

The release that makes the **hosted** API usable by someone who has never heard
of vLLM. Nothing here changes self-hosting.

### Added
- **`captchakraken-mcp`, a new npm package**, published from this repo as a
  third workspace (`mcp/`). Signs you in with GitHub, mints a solving key, and
  reports balance, usage and a top-up link. `npx captchakraken-mcp`.

  It is a **separate install** from `captchakraken`, deliberately: `npx`
  resolves by package name, and the MCP's dependencies have no business on
  every browser-driver install. It also keeps its own version line — 0.1.0
  here — so a solver change does not force an MCP release.
- `CaptchaKrakenAPIError` is exported from the TypeScript port, carrying
  `code`, `resolutionUrl` and `retryAfterSeconds`. Branch on `code`; the prose
  is not a contract.
- The credentials file may now carry the **endpoint** alongside the key
  (`VLLM_BASE_URL=` or `CAPTCHA_KRAKEN_BASE_URL=`).

### Changed
- **Hosted-API refusals now explain themselves.** Running out of credits used
  to produce `vLLM 402 Payment Required at https://api.captchakraken.com/...`,
  naming infrastructure the user never installed. Out of credits, rate limited,
  account suspended, request too large, and abandoned attempts now each produce
  a sentence naming CaptchaKraken and the URL that resolves it, in both ports.
  429 honours `Retry-After`.

  An unrecognised code still produces a useful message — it carries the
  server's own text through — rather than falling back to something generic.
- **`create_api_key` no longer returns the secret.** It writes
  `~/.captchakraken/credentials` at 0600 and reports the path, so a live key
  cannot reach an agent transcript. It writes the endpoint at the same time.
- **A key from the MCP no longer needs `VLLM_BASE_URL` set.** `base_url()`
  reads the credentials file when the env var is absent, so a hosted user's
  first solve reaches `api.captchakraken.com` instead of dialling a local port
  with nothing behind it. An explicit `VLLM_BASE_URL` still wins, and a machine
  with no credentials file still defaults to localhost.

### Unchanged, on purpose
- Self-hosting. A local vLLM sends no error envelope, so it still produces the
  old message, bearer-token hint and all. A bare-token credentials file still
  yields no endpoint, so nobody who hand-wrote a local key into that file gets
  silently redirected at our servers.

## [2.3.0] — 2026-07-24

### Added
- **A Python page driver — you can now solve captchas from Python end to end.**
  Until now the Python port was image-in / actions-out: you handed it a PNG and
  got back click/drag actions, and something else had to own the browser.
  Everything that actually drives a page lived only in the TypeScript driver, so
  Python callers could not use the solver against a live site at all.

  ```python
  from playwright.sync_api import sync_playwright
  from captchakraken import PageSolver

  result = PageSolver().solve(page)   # detects, solves, clicks, submits
  ```

  `captchakraken.page_solver` mirrors `js/src/solver.ts`: the same detection
  order, the same freshness guard, the same multi-round driver for reCAPTCHA's
  dynamic 3×3, the same under-selection retry, and the same submit policy.
  Verified against live reCAPTCHA — on a real dynamic 3×3 it drives the rounds
  and submits on `done`, exactly as the TypeScript driver does.

  **The split is identical on both sides**: vision, CV and prompting stay in
  Python (`solver.py`, `planner.py`, `tool_calls/`); the driver only finds the
  challenge and clicks. The TypeScript driver reaches the Python half by
  spawning the CLI; this one calls the same functions in-process — no
  subprocess, no CV worker to leak. That is the *only* intended difference, so
  the two cannot drift on anything that decides accuracy.

  Imports no browser package: pass any Playwright-compatible page (`playwright`,
  `patchright`, camoufox) and it duck-types the slice it needs.

  **Synchronous only.** An async mirror is not yet written — a sync Playwright
  handle cannot be driven from inside an event loop, so `AsyncCamoufox` and
  `async_playwright` users are not covered by this release.
- **`camoufox` integration.** `pip install "camoufox[captcha]"` then
  `from camoufox.captcha import solve_captcha`. Requests are tagged
  `camoufox/<version>` for attribution.
- **Human mouse trajectories in Python** (`captchakraken.trajectory`). Same
  `(points, cumulative_timings)` contract as the TypeScript driver's cursory-ts
  call, so pacing is driver-independent: Fitts's-law duration, Bezier arc,
  ease-in-out velocity, speed-scaled jitter, and overshoot-and-correct on longer
  moves. An independent implementation, not a port — cursory-ts selects from a
  bundled corpus of recorded human traces, which is that package's own asset.

### Fixed
- `__version__` in the Python package said `2.0.0` through the entire 2.2.x
  line. It now tracks the real version.

Three more, all found by running the new driver against live reCAPTCHA rather
than against fixtures — none of them were visible in the hermetic tests:

- **An already-passed captcha raised instead of returning.** If the vendor had
  already cleared the widget, nothing was detectable to solve and we had not
  interacted, so the render-wait branch ran out and raised
  `NoCaptchaFoundError`. That is the *common* case behind a good stealth
  browser — camoufox frequently clears reCAPTCHA on the checkbox alone — so the
  best possible outcome was being reported as an error the caller had to catch.
- **`scroll_into_view_if_needed()` inherited Playwright's 30 s default** and runs
  once per action plus once per submit. On a challenge iframe that is
  mid-animation it waits for stability and burns the full 30 s each time, which
  turned a ~5 s solve loop into minutes. Now bounded to 2 s; the element has
  just been screenshotted, so there is nothing to lose.
- **`overall_solve_timeout_ms` was not actually a budget.** It was checked only
  at the top of each attempt, so a single slow attempt overran it without bound
  — nothing looked at the clock again until that attempt returned. A camoufox
  session was observed running past ten minutes against a nominal 120 s timeout.
  The deadline is now enforced inside the long-running loops (each action
  executed, each round of the dynamic grid driver), so the configured budget is
  the real ceiling. *The TypeScript driver has the same structural gap and has
  not been changed here.*
- **Mouse moves could wedge camoufox permanently.** camoufox humanises every
  `mousemove` into its own trajectory, guards the intermediate points against
  the window bounds, and then dispatches the requested destination *unguarded*
  ("always finish exactly on the requested destination"). A destination outside
  the window fires as an exit event rather than `eMouseMove`, so no
  hit-renderer ack returns; dispatch is serialised on a process-global
  activation chain, so that one missing ack hangs **every later input event
  forever**. The symptom is `page.mouse.move()` never returning — 0% CPU, no
  in-flight work, a solve that looks dead. Same failure family as camoufox #225.
  The driver now resolves the real window (`viewport_size`, falling back to
  asking the page for `innerWidth/innerHeight`, since camoufox reports
  `viewport_size = None`) and clamps a pixel inside it. With this, three
  consecutive live reCAPTCHA solves through camoufox returned real tokens.
- **Progress output was invisible when piped.** Python block-buffers stdout when
  it is not a TTY, so every line of a minutes-long solve appeared at once on
  exit — a working run looked exactly like a hung one in a log file or CI.

## [2.2.1] — 2026-07-24

Point release. No API breaks; every change below is either a fix for a puzzle
class that silently failed, or an opt-in header/credential path that is absent
unless you deliberately set it.

### Fixed
- **Drag puzzles failed as "unsupported" against the current adapter.** The
  LoRA was retrained on the content schema (`action: drag`, `drags[]`,
  lowercase) while `planner.py` still asked for the legacy output/PascalCase
  schema. The model answered in a hybrid, the parser dropped it, and every drag
  puzzle failed — with no test going red. The inference prompt is now synced to
  the trained schema, and the parser accepts every `simulate_drag` shape the
  model emits rather than one canonical form.
- **30-second stale-element hangs in the JS driver.** A handle captured before
  the frame re-rendered was awaited until the Playwright timeout; the solver now
  detects the detach and re-acquires instead of blocking the whole solve.

### Added
- **Pinned serving manifest** (`pinned_model.json`): the base model, the LoRA
  adapter and revision, and the SHA-256 of each serving prompt, asserted in CI
  (`tests/test_pinned_model.py`). Editing a serving prompt now fails CI until
  someone consciously re-pins, which forces the question "does the pinned
  adapter still expect this prompt?" — the check that would have caught the drag
  regression above on the day it landed.
- **Credentials file.** When neither `CAPTCHA_KRAKEN_API_KEY` nor `VLLM_API_KEY`
  is set in the environment, the key is read from
  `~/.captchakraken/credentials` (override the directory with
  `CAPTCHA_KRAKEN_STATE_DIR`). Env always wins, so nothing that works today
  changes; this only removes the need to keep a bearer token in your shell
  profile.
- **Hosted-API attribution headers**, both optional and absent unless set, so
  self-hosted users are unaffected:
  - `X-CK-Client` (from `CAPTCHA_KRAKEN_CLIENT`) — which integration issued the
    solve, e.g. `camoufox/0.4.11`. Attribution only: it is caller-supplied and
    is never priced on.
  - `X-CK-Session` (from `CAPTCHA_KRAKEN_SESSION`) — groups the 1..N inference
    rounds of one captcha into a single billable attempt. The JS driver mints a
    UUID per `solve()` and reuses it across every CLI invocation in that solve.
  Both values are sanitized before they reach the wire — a CR/LF in the
  environment would otherwise splice arbitrary headers into the request.
- **Fleet-routing priority header.** Setting `CAPTCHA_REQUEST_PRIORITY` to a
  positive int sends `X-JH-Priority: <n>`, which a fleet front-end can route on
  to keep throwaway traffic (e.g. a CI gate) off the production GPU.
  Deliberately a header, not vLLM's request-body `priority` field, which is
  lower-is-higher and would misorder against the server's own scheduling
  classes.

### CI
- The hermetic suite now runs in full on every PR; the dead v1-only tests are
  skipped **visibly** rather than silently collected. New coverage: solver
  contract, grid parse, routing headers, credentials file, pinned manifest.

## [2.2.0] — 2026-07-15

### Added
- **Freshness guard — never act on a stale frame.** reCAPTCHA/hCaptcha fade
  fresh tiles in over ~1s; if the frame changed *while the model was
  generating*, its answer described an "undeveloped" frame whose tiles no longer
  lined up. The solver now re-screenshots after every model query and diffs it
  against the frame it sent (`check-movement`); on a change it discards the stale
  answer and re-solves on the developed frame. Covers both the one-shot path and
  the reCAPTCHA 3×3 dynamic driver. Tunable via `staleFrameReSolveEnabled` /
  `staleFrameDiffThreshold` / `maxStaleFrameReSolves`; `check-movement` was added
  to the persistent CV worker so the check runs on the warm process.
- **Unified `captchakraken fetch` updater.** One command pulls the latest model
  from the HuggingFace org (https://huggingface.co/CaptchaKraken) *and* upgrades
  the vLLM serving stack, then restarts a running local server. Flags:
  `--weights-only`, `--engine-only`, `--no-restart`, `--dry-run`. Shell
  equivalent: `./setup.sh --update`.
- **Documentation hub.** Most of the README moved into a browsable
  [`docs/`](docs/README.md) tree (self-hosting, usage, how-it-works, performance,
  roadmap, licensing); the README is now a slim overview + quickstart.

### Fixed
- **License metadata corrected.** Both ports previously declared `GPL-3.0`, which
  contradicted (and would have *overridden* with a permissive license) the
  source-available `LICENSE` that prohibits selling the solve. The npm and PyPI
  packages now declare the CaptchaKraken Source-Available License and ship the
  `LICENSE` file.

### CI
- The Python job is now a **no-regression gate**: grid detection + the freshness
  check + the fetch command, run on every PR (still hermetic — no GPU/network).

## [2.0.0] — 2026-06-07

### ⚠️ Breaking
- **Complete solver rewrite.** v2 replaces the v1 architecture (SAM3 grounding +
  general multi-provider LLMs: Gemini / OpenRouter / Ollama) with a single
  purpose-built **Qwen3.5-9B grid LoRA** served on a local **vLLM** server.
- **Solver API / config changed.** `apiProvider`, multi-provider `model`, and
  provider `apiKey` options are gone. The solver now reads just two env vars —
  `VLLM_BASE_URL` and `CAPTCHA_KRAKEN_API_KEY` — and defaults to the published
  grid LoRA, so `new CaptchaKrakenSolver()` needs no model/provider.
- v1's `transformers` / `torch` / SAM3 dependencies are removed from the solver
  venv (available on the `v1-old-architecture` branch).

### Added
- **Bring-your-own browser — zero browser dependency.** The package no longer
  depends on any browser library in any form: `@jobharvest/camoufox-js`,
  `patchright`, and `patchright-core` are gone (not even devDependencies). The
  public API types `solve(page)` against an implementation-neutral, self-contained
  structural Playwright `Page` interface defined by the package itself (not
  imported from `playwright-core`), so any Playwright-compatible launcher works —
  vanilla `playwright`, `patchright`, `camoufox-js`, etc. Install whichever one
  you want yourself and hand the solver its `Page`. (The live solve-and-record
  tests moved to the parent `CaptchaKrakenFinetune` repo, which owns the launcher.)
- **Puppeteer support via `fromPuppeteer()` adapter.** Puppeteer isn't
  Playwright-API-compatible, so the package exports a thin `fromPuppeteer(page)`
  wrapper that bridges the few differing methods (`viewport`/`viewportSize`,
  `waitForTimeout`, `getAttribute`/`textContent`/`scrollIntoView` via `evaluate`,
  selector-state options). Wrap a Puppeteer page once and pass it to `solve()`.
  All four launchers (Playwright, Patchright, camoufox-js, Puppeteer) are tested
  end-to-end against the live reCAPTCHA demo.
- **`install.sh`** — one-command, hardware-gated setup. Detects NVIDIA VRAM or
  Apple-silicon unified memory, picks FP8 8-bit (≥22 GB) vs AWQ 4-bit (11–22 GB),
  refuses to install below the serve floor (with download-anyway / get-notified
  options), pulls base + grid LoRA, and writes `captchakraken.env`.
- **Source-available LICENSE** — build *with* the model (scrapers, stealth
  browsers, data collection); don't sell captcha-solving as a service or ship
  thin wrappers. See [LICENSE](LICENSE).
- **CONTRIBUTING.md** and a **CI workflow** (`.github/workflows/ci.yml`) running
  hermetic grid-detection tests + a TypeScript build on every PR (no GPU/network).
- **Hermetic grid-detection tests** (`test_grid_detection_ci.py`) that synthesize
  grids in memory — the CI guard for the core `find_grid` invariant.
- **Demo recorder** (`tests/record_demos.spec.ts`) — drives a real browser
  against the live model, tags reCAPTCHA attempts 3×3 vs 4×4, skips/retries
  out-of-scope hCaptcha puzzles, and records videos of successful solves plus a
  per-type solve-rate summary.

### Solver / model
- Grid LoRA (`JobHarvest/qwen3.5-9b-grid-lora`) exact-tile accuracy on held-out
  real data: reCAPTCHA 3×3 **94.7%**, hCaptcha 3×3 property **86.7%**,
  reCAPTCHA 4×4 **76.2%** (overall **85.8%**).
- reCAPTCHA dynamic 3×3 (multi-round in-place refresh) and 4×4 one-shot grids,
  hCaptcha 3×3 property grids, and the checkbox / Turnstile flows are solved
  end-to-end. Non-grid hCaptcha puzzles are detected and safely skipped.

### Coming soon
- Hosted cloud API (no GPU required), smaller quantizations, and non-grid
  hCaptcha puzzle support. See the README roadmap.
