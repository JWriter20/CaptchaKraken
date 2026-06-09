# Contributing to CaptchaKraken

Thanks for your interest! Contributions are welcome — bug fixes, better grid
detection, solver robustness, docs, and especially **real labeled samples** for
the puzzle types we're still weak on.

By contributing you agree that your contributions are licensed under the
repository's [LICENSE](LICENSE) (the CaptchaKraken Source-Available License).

## Ground rules

- **The model is Qwen3.5-9B.** Never reference Qwen2 / Qwen2.5 / Qwen-VL anywhere;
  grep before any change that touches serving or the planner.
- **`find_grid` is the foundation.** It's pure OpenCV (no model) and the thing CI
  guards most tightly — if you touch it, keep the per-type detection rates from
  regressing (see Tests & CI).
- **Docs travel with code.** Keep the README and the CLI's docs current in the
  same change as any layout/flow change.

## Dev setup

```bash
git clone --recursive git@github.com:JWriter20/CaptchaKrakenJS.git
cd CaptchaKrakenJS
npm install        # builds the solver + a local CLI venv (postinstall)
npm run build
```

This package ships **no browser** — it types its public API against an
implementation-neutral Playwright `Page`, and you bring your own
Playwright-compatible launcher (vanilla `playwright`, `patchright`,
`camoufox-js`, …). Install whichever one you want before driving a real solve.

To run a solver against a model you'll need a vLLM server — see
[`install.sh`](install.sh) and the README "Self-hosting" section.

## Tests & CI

CI runs a small, fast suite on every PR (no GPU, no network) — primarily the
**grid-detection** checks (in the `CaptchaKraken-cli` submodule), which are the
foundation of the whole solve, plus a TypeScript build of the solver. Run them
locally before opening a PR:

```bash
# Python: find_grid / grid-detection unit tests (fast, deterministic)
cd CaptchaKraken-cli
python -m pytest tests/test_grid_detection.py -q

# Full corpus benchmark (report-only — prints per-type detection rates)
python -m pytest tests/test_find_grid_corpus.py -s
```

```bash
# TypeScript: type-check / build the solver
npx tsc --noEmit -p tsconfig.json
```

This package has **no browser end-to-end tests of its own** — it never launches a
browser. The live solve-and-record tests (`record_demos.spec.ts` et al.) live in
the parent `CaptchaKrakenFinetune` repo, which owns a Playwright launcher and
drives the built solver. Grid detection is exercised by the Python tests above.

## Pull requests

- Branch off `main`, keep PRs focused, and describe what you changed and how you
  verified it.
- If you touched grid detection, paste the `test_find_grid_corpus.py` per-type
  table before/after so reviewers can see the delta.
- New end-to-end solving capability? Include a short clip or the
  `record_demos_summary.json` numbers from a local run of the demo recorder in
  the parent `CaptchaKrakenFinetune` repo
  (`npx playwright test tests/record_demos.spec.ts` there).

## What we'd love help with

- More **real labeled samples** for under-represented hCaptcha grid prompts.
- Robustness on **reCAPTCHA 4×4** (our weakest grid type end-to-end).
- Out-of-scope puzzle types (drag/path/tetris) — currently detected and skipped.
- Smaller / faster quantizations so lower-VRAM hardware can self-host.

Questions? Open an issue. Please don't open PRs that add a paid captcha-solving
API or thin wrapper — those are outside what the license permits (see
[LICENSE](LICENSE) §3).
