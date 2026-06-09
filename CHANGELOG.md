# Changelog

All notable changes to CaptchaKraken are documented here. This project follows
semantic versioning; v2 is a major, **breaking** release.

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
