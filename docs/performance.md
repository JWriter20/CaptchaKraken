# 📊 Performance

How accurate the model is, how fast it runs on your hardware, and why real-world
solve rates depend on more than the model.

> v2.2 is faster than earlier v2 releases <!-- TODO: add measured speedup --> —
> the numbers below are the ones we last measured; figures that predate the
> latest LoRA are flagged for re-confirmation.

## Accuracy

On our hand-labeled real captcha set, the model picks the exactly-correct tiles
**94.7%** <!-- TODO: re-confirm on latest LoRA --> of the time for reCAPTCHA 3×3
(86.7% for hCaptcha, 76.2% for the harder 4×4 grid — **85.8%** overall).
<!-- TODO: re-confirm on latest LoRA -->

**What about real solve rates in a browser?** They vary a lot depending on your
**IP reputation and browser setup** (see [below](#rate-limiting--ip-reputation)).
On a standard setup, expect **roughly 50%** <!-- TODO: re-confirm on latest LoRA -->
end-to-end for reCAPTCHA. A clean IP and good stealth browser do better; a
flagged IP does much worse — providers reject even correct answers once they
distrust your IP.

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

> ⏳ **Below ~30 tokens/sec, self-hosting feels sluggish.** If that's your card,
> consider the upcoming **hosted cloud API** instead — it serves the 8-bit model
> at **~100 tokens/sec** with no GPU to run. ⭐ [Star the repo](https://github.com/JWriter20/CaptchaKraken)
> to be notified when it launches. `setup.sh` estimates your speed from your
> device's bandwidth (NVIDIA / AMD / Apple) and flags this automatically.

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
