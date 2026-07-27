# 📊 Performance

How accurate the model is, how fast it runs on your hardware, and why real-world
solve rates depend on more than the model.

## Accuracy

**Measured 2026-07-27** against the deployed `CaptchaKraken_v1.1` adapter, over
the full customer path — HTTPS → gateway → vLLM — rather than against a local
checkpoint, so it includes anything the serving stack does to a request.

Exact set match: every correct tile selected and no incorrect ones. Partial
credit is not given, because a partially-correct grid answer is a rejected
captcha.

| Challenge | n | Exact match | Median latency |
|---|---:|---:|---:|
| reCAPTCHA 3×3 | 80 | **81.2%** | 1.6 s |
| hCaptcha 3×3 property | 80 | **58.8%** | 1.6 s |
| reCAPTCHA 4×4 | 80 | **0.0%** | 1.6 s |
| **Overall** | **240** | **46.7%** | **1.6 s** |

Latency spread over the 240 requests: p10 0.9 s, median 1.6 s, p90 2.1 s.
Median prompt 348 tokens, completion 7–19 tokens.

### reCAPTCHA 4×4 is broken, and here is the evidence

0/80 is not a harness artifact. The model selects the right *number* of tiles —
median 6 against a median ground truth of 6 — but the wrong ones, at a **mean
IoU of 0.33**. Every answer was regraded under transpose, horizontal flip,
vertical flip, 180° rotation and ±1 index offset; the best of those moved the
mean IoU to 0.34, so it is not an indexing or orientation mismatch.

A 4×4 reCAPTCHA is one large photograph cut into sixteen tiles, and the task is
"select every tile containing part of the object". That is a segmentation
problem wearing a grid costume, and it is different enough from "pick the
matching photographs" that the adapter has not learned it. It is the first
target of the next Abyss training run.

### Two label bugs found while measuring this

Both are in `cleanSamples/test/test_solutions.json`, and both silently corrupt
any evaluation that trusts the file naively:

1. **Every one of the 288 real reCAPTCHA 4×4 images has a 3×3 instruction.**
   The `instruction` field says `Grid: 3x3 (9 cells)` while the ground truth
   names cells up to 16. Grading against that field sends the model a 3×3
   prompt and marks it wrong for not answering a 4×4. `src/testing/grade.py`
   already sidesteps this — `synthesize_instruction()` rebuilds the prompt from
   `canonical_instruction(puzzle_type)` — so any new harness must do the same.
2. **`rows`/`cols` disagree with `puzzle_type` on those same records.** Trust
   `puzzle_type`; it is the only field that agrees with the answer.

### Why the old numbers are gone

This page used to claim 94.7% for reCAPTCHA 3×3 and 85.8% overall, carried
across releases behind a `TODO: re-confirm on latest LoRA`. Re-measuring did
not reproduce them. They are removed rather than annotated, because a stale
accuracy figure with a footnote is still the number a reader quotes.

**Browser solve rates are a different measurement.** In live runs against
`google.com/recaptcha/api2/demo` on 2026-07-27, Camoufox cleared 3/3 challenges
and Holo 1/3, each taking 5–8 model rounds — reCAPTCHA replaces tiles after
every click, and each replacement is a fresh puzzle. Both runs came from one
datacenter IP within one hour, which is the least favourable condition
available; see [below](#rate-limiting--ip-reputation).

## A note on speed

Solve time is dominated by how fast your hardware generates tokens, and LLM
generation is **memory-bandwidth bound** — each token streams the model's weights
through memory once. So speed tracks your GPU/SoC **memory bandwidth**, not its
capacity: roughly `tokens/sec ≈ bandwidth × ~50% ÷ bytes-per-token`, where the
8-bit (FP8) model reads ~9 GB/token and the 4-bit (AWQ) ~4.5 GB/token. (Measured
on a 5090: ~100 tok/s on FP8, ~200 tok/s on AWQ — both match this formula.)
<!-- TODO: re-confirm on latest LoRA -->

Estimated throughput for common devices:

| Device | Memory bandwidth | FP8 (8-bit) | AWQ (4-bit) |
|---|---|---|---|
| NVIDIA H100 | ~3.35 TB/s | ~186 | ~370 |
| NVIDIA A100 | ~2.0 TB/s | ~111 | ~222 |
| **NVIDIA RTX 5090** | **~1.79 TB/s** | **~100** | **~200** |
| NVIDIA RTX 4090 | ~1.0 TB/s | ~56 | ~112 |
| NVIDIA RTX 5080 / 3090 | ~0.95 TB/s | ~53 | ~105 |
| NVIDIA RTX 5070 Ti | ~896 GB/s | ~50 | ~100 |
| AMD RX 7900 XTX | ~960 GB/s | ~53 | ~106 |
| NVIDIA RTX 3080 / 4080 | ~0.7–0.76 TB/s | ~40 | ~80 |
| Apple M2 / M3 Ultra | ~800 GB/s | ~44 | ~88 |
| NVIDIA RTX 5070 | ~672 GB/s | ~37 | ~74 |
| Apple M5 Max | ~614 GB/s | ~34 | ~68 |
| Apple M4 Max | ~546 GB/s | ~30 | ~60 |
| NVIDIA RTX 4070 | ~504 GB/s | ~28 ⚠️ | ~56 |
| Apple M1–M3 Max | ~400 GB/s | ~22 ⚠️ | ~44 |
| Apple M5 Pro | ~307 GB/s | ~17 ⚠️ | ~34 |
| Apple M4 Pro | ~273 GB/s | ~15 ⚠️ | ~30 |
| Apple M5 (base) | ~154 GB/s | ~8 ⚠️ | ~17 ⚠️ |

(Rough estimates at ~50% bandwidth efficiency; real numbers vary with batching,
KV-cache length, and driver. AWQ is faster but slightly less accurate.) Sources:
NVIDIA / AMD / Apple spec sheets.

**Cards that comfortably self-host** (≥ 30 tok/s): NVIDIA 5090 · 5080 · 5070 Ti ·
5070 · 4090 · 4080(Ti) · 3090 · 3080; AMD 7900 XTX / 7900 XT; Apple **Ultra**
chips and the **M4/M5 Max**. Most other cards fall under ~30 tok/s on the 8-bit
model — 4070 and below, older Apple **Max** chips (fine on the 4-bit model), Apple
**Pro**/base laptops, and older mid-range AMD.

> ⏳ **Below ~30 tokens/sec, self-hosting feels sluggish.** If that is your card,
> use the **hosted API** instead — it runs Abyss on our fleet with no GPU of
> yours involved, and the 240 requests measured above returned in a median
> 1.6 s end to end including the network. Sign in at
> [captchakraken.com](https://captchakraken.com/signin). `setup.sh` estimates
> your speed from your device's bandwidth (NVIDIA / AMD / Apple) and flags this
> automatically.

## Rate limiting & IP reputation

> ⚠️ **Solving many captchas fast from one IP lowers your success rate.** This is
> normal anti-abuse behavior — once a provider distrusts an IP, it rejects
> submissions **even when the answer is correct** and serves harder challenges.

CaptchaKraken only produces the answer. Managing your IP reputation is **your
job**. In production you'll usually want to:

- **Use rotating / residential proxies** instead of one IP.
- **Space out requests** — avoid rapid bursts.
- **Rotate the IP** when you notice correct answers being rejected or challenges
  getting harder, rather than retrying on the same one.

This only affects whether the provider *accepts* a solve — it doesn't change the
model's accuracy.

---

← Back to [docs index](./README.md)
