# Examples (Python)

Two runnable demos that drive a real stealth browser
([camoufox](https://github.com/JWriter20/camoufox)) to a live captcha demo site,
screenshot the challenge, run the **engine** on it, and print token speed /
total time / outcome:

| File | Site |
|---|---|
| `demoRecaptcha.py` | Google reCAPTCHA v2 demo |
| `demoHcaptcha.py` | hCaptcha demo |

> The Python port is the engine (detection + planner). These demos validate the
> engine + model + server on a real challenge frame. Full click-replay and
> multi-round verification in a live page are what the TypeScript port
> (`captchakraken`) does end-to-end.

## Setup

```bash
cd python
pip install -e ".[serve]"         # engine + serving stack (use ".[]" for a remote server)
pip install camoufox              # example-only dep
```

### The camoufox binary (from your fork)

Uses the **camoufox binary from the fork's releases**:
[JWriter20/camoufox → Releases](https://github.com/JWriter20/camoufox/releases).

1. Download the latest release asset for your OS/arch and extract it.
2. Point the demo at the extracted `camoufox` executable:

```bash
export CAMOUFOX_BINARY=/path/to/camoufox/camoufox      # your fork binary
```

If `CAMOUFOX_BINARY` is unset, camoufox falls back to its default binary
(`python -m camoufox fetch`).

### Point at a model

```bash
source ../captchakraken.env       # VLLM_BASE_URL + CAPTCHA_KRAKEN_API_KEY
```

## Run

```bash
python examples/demoRecaptcha.py
python examples/demoHcaptcha.py
HEADLESS=0 python examples/demoRecaptcha.py    # watch the browser
```

## Reading the report

```
  result        : ✓ engine produced a solution
  click plan: 4 tile(s)/target(s)
  total time    : 6.1s (solve: 3.4s)
  tokens        : 812 in / 34 out
  gen speed     : ~10.0 tok/s
  reason        : <only on failure — unsupported puzzle, unreachable server, …>
```

`gen speed` = model output tokens ÷ solve seconds. Failure reasons the harness
reports: unreachable vLLM server, an unsupported hCaptcha puzzle (drag/video),
the challenge iframe never appearing, or the model returning no matching tiles.
