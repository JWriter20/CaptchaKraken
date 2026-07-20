# 🖥️ Self-hosting

Run the whole solver on your own GPU or Apple-silicon machine — no third-party
solving service involved.

> 💡 **No GPU?** A hosted cloud API (no model to run) is coming — ⭐ [star the
> repo](https://github.com/JWriter20/CaptchaKraken) to be notified. For now
> CaptchaKraken runs on your own hardware.

## One-command install

```bash
./setup.sh
```

This checks your available memory, picks the right model size, downloads it plus
the grid model, and writes a `captchakraken.env` config file.

| Your memory | Model it picks |
|---|---|
| **≥ 22 GB** | `Qwen3.5-9B-FP8-dynamic` (8-bit) — best accuracy |
| **11–22 GB** | `Qwen3.5-9B-AWQ-4bit` (4-bit) — lighter, slightly less accurate |
| **< 11 GB** | Too small to run — the installer stops and explains your options |

If your hardware is too small, you can still `./setup.sh --download-only`
(e.g. to copy the model to a bigger server later), or watch the repo for smaller
models and the cloud API. Force a size with `./setup.sh --quant fp8|awq`.

> ⏳ Wondering whether your card is fast enough? See
> [Performance → throughput by device](./performance.md), which lists estimated
> tokens/sec per GPU/SoC. `setup.sh` estimates the same from your device's
> bandwidth and flags cards that will feel sluggish.

## Start the server (you usually don't have to)

The server is **hands-off**: after `setup.sh`, it **auto-starts on your first
solve** and stays up. You never run `vllm serve` yourself. To manage it
explicitly:

```bash
source captchakraken.env
captchakraken server start     # background, waits until healthy
captchakraken server status    # endpoint + which model is served
captchakraken server stop
```

Under the hood that runs the equivalent of (assembled from your env, so it
tracks whatever model you configured — keep `--enable-tower-connector-lora` or
the vision half of the LoRA is dropped and accuracy collapses):

```bash
vllm serve "$CAPTCHA_BASE_MODEL" \
  --reasoning-parser qwen3 \
  --enable-lora --enable-tower-connector-lora \
  --max-lora-rank 64 --max-model-len 65536 \
  --gpu-memory-utilization 0.80 --trust-remote-code \
  --port 8000 \
  --lora-modules "$CAPTCHA_LORA_NAME=$CAPTCHA_LORA_ADAPTER"
```

Defaults: base `Qwen/Qwen3.5-9B` (or a quantized variant `setup.sh` picks for
your VRAM) + the unified adapter `CaptchaKraken/CaptchaKraken_v1`, served
as `captcha`.

## Configuration

The solver only needs **two** environment variables to talk to a server (both
written by `setup.sh` into `captchakraken.env`):

| Variable | Meaning | Default |
|---|---|---|
| `VLLM_BASE_URL` | Inference endpoint — your local vLLM server, or the hosted cloud endpoint when it launches. | `http://localhost:8000/v1` |
| `CAPTCHA_KRAKEN_API_KEY` | Bearer token — your server's key today, your account key on the cloud API later. | `EMPTY` |

Advanced overrides — only needed to change **which** model is served (the CLI
itself is model-agnostic):

| Variable | Meaning | Default |
|---|---|---|
| `CAPTCHA_BASE_MODEL` | Base weights vLLM loads (HF id or local path) | `Qwen/Qwen3.5-9B` |
| `CAPTCHA_LORA_ADAPTER` | Captcha adapter served on top (HF id or local path) | `CaptchaKraken/CaptchaKraken_v1` |
| `CAPTCHA_LORA_NAME` | Served adapter name the client requests as `model` | `captcha` |
| `VLLM_PORT` | Port the local server binds | `8000` |
| `CAPTCHA_KRAKEN_AUTOSTART` | `0` disables auto-starting a local server on first use | `1` |

Point `VLLM_BASE_URL` at a server you already run to skip local management
entirely — a remote endpoint is never auto-started for you.

## Updating

Get new **model revisions** and **engine fixes** with one command — no full
reinstall:

```bash
captchakraken fetch
```

`fetch` does three things in order:

1. **Pulls the latest weights** — re-downloads the configured captcha LoRA + base
   model from the HuggingFace org
   [huggingface.co/CaptchaKraken](https://huggingface.co/CaptchaKraken). A no-op
   if you're already on the latest revision; pulls the new commit otherwise.
2. **Upgrades the serving stack** — `pip install -U vllm huggingface_hub` in the
   current environment.
3. **Restarts a running local server** so the freshly pulled weights + engine
   actually take effect. Remote endpoints you manage are left untouched.

Flags:

| Flag | Effect |
|---|---|
| `--weights-only` | Just re-pull the weights (skip the vLLM upgrade). |
| `--engine-only` | Just upgrade vLLM (skip the weight download). |
| `--no-restart` | Don't bounce a running local server. |
| `--dry-run` | Print the plan as JSON and do nothing (no network, no pip). |

On an already-set-up box, the shell equivalent is:

```bash
./setup.sh --update
```

which sources your `captchakraken.env` and hands off to `captchakraken fetch`.

---

← Back to [docs index](./README.md)
