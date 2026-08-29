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

One repo, two ports: `js/` (TypeScript browser driver → npm) and `python/` (the
`captchakraken` engine → PyPI).

```bash
git clone git@github.com:JWriter20/CaptchaKraken.git
cd CaptchaKraken

# TypeScript port
cd js && npm install && npm run build && cd ..

# Python port
cd python && pip install -e ".[dev]" && cd ..
```

The `js` package ships **no browser** — it types its public API against an
implementation-neutral Playwright `Page`, and you bring your own
Playwright-compatible launcher (vanilla `playwright`, `patchright`,
`camoufox-js`, …). Install whichever one you want before driving a real solve.

To run a solver against a model you'll need a vLLM server — see
[`setup.sh`](setup.sh) and the README "Self-hosting" section.

## Tests & CI

Two gates run on every PR.

**1. A hermetic suite** (no GPU, no network): the whole Python `pytest` suite —
primarily the **grid-detection** checks in `python/`, which are the foundation
of the whole solve — plus a TypeScript build of the driver. Run it locally
before opening a PR:

```bash
# Python: find_grid / grid-detection unit tests (fast, deterministic)
cd python
python -m pytest tests/test_grid_detection_ci.py -q

# Full corpus benchmark (report-only — prints per-type detection rates)
python -m pytest tests/test_find_grid_corpus.py -s
```

```bash
# TypeScript: type-check / build the driver
cd js
npx tsc --noEmit -p tsconfig.json
```

**2. A driver gate.** Both ports are driven end to end through a real browser
against a fixture suite, and the result comes back as the `tier3/driver-gate`
commit status plus a PR comment with per-port and per-vendor pass rates. The
fixtures and the model live in a private repo, so this one runs there and
reports back — you cannot run it locally, and you do not need to. It is the gate
that catches what a type-check cannot: a selector that moved, a click landing
off-widget, or one port asking for something the other does not.

This package has **no browser tests of its own** — it never launches a browser,
and it ships none. Everything you can run locally is the hermetic suite above.

> On a **fork PR** the driver gate cannot run: GitHub does not expose the
> dispatch secret to a fork's workflow, so the check sits as pending. That is
> expected. A maintainer re-runs it by pushing your branch into this repo.

## Pull requests

- **Branch off `dev`, and open your PR against `dev`.** `main` is the release
  branch and only ever takes a `dev` → `main` merge, so a PR straight into
  `main` will be redirected.
- Keep PRs focused, and describe what you changed and how you verified it.
- If you touched grid detection, paste the `test_find_grid_corpus.py` per-type
  table before/after so reviewers can see the delta.
- New end-to-end solving capability? Say which vendor and puzzle it covers and
  how you drove it. The driver gate above will exercise it; you do not need to
  produce your own recording.

## What we'd love help with

- More **real labeled samples** for under-represented hCaptcha grid prompts.
- Robustness on **reCAPTCHA 4×4** (our weakest grid type end-to-end).
- Accuracy on the **freehand hCaptcha puzzles** — connect-the-path, the
  numbered-line and missing-piece drags. Every hCaptcha family now routes and is
  driven; these are the ones where the model is least reliable.
- Smaller / faster quantizations so lower-VRAM hardware can self-host.

Questions? Open an issue. Please don't open PRs that add a paid captcha-solving
API or thin wrapper — those are outside what the license permits (see
[LICENSE](LICENSE) §3).
