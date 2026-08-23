# 📊 Performance

How accurate the model is, how fast it runs on your hardware, and why real-world
solve rates depend on more than the model.

## Accuracy

> 🔬 **Being re-measured — no accuracy figures are published right now.**
>
> The numbers that stood here were taken on **2026-07-27** against the deployed
> `CaptchaKraken_v1.1` adapter. They excluded test images that appeared in a
> labeled-train augment pool — but that exclusion was not the same thing as a
> clean holdout, and the eval split of the day allowed some hand-labeled
> captures into training. So the figures measured memorisation alongside skill,
> by an amount that varied per puzzle type and was largest exactly where the
> headline number was highest.
>
> The split has since been replaced: **every** real capture is held out and
> nothing hand-labeled trains, which is the only arrangement in which the real
> captures can still detect generator drift. Figures return here after the next
> full training run measures against it.

When they return, this is the method they will use, unchanged from before:
exact set match — every correct tile selected and no incorrect ones, no partial
credit, because a partially-correct grid answer is a rejected captcha — taken
over the full customer path (HTTPS → gateway → vLLM) rather than against a
local checkpoint, so it includes anything the serving stack does to a request.

Latency is unaffected by any of the above and still holds: p10 0.84 s, median
1.36 s, p90 1.76 s end to end including the network, median prompt 341 tokens.

### Grids must be sent with the cell numbers drawn on

This is the single biggest thing to get right in a client, and getting it wrong
does not look like a bug — it looks like a bad model.

`solver.py` runs `find_grid`, renders `get_numbered_grid_overlay` (red labels,
top-right, all cells 1..N — byte-identical to the overlay
`scripts/build_grid_overlays.py` used to build the training data) and sends
*that* image. The model reads the labels. It does not infer a numbering from
tile positions, because it was never trained to.

Measured on the same 300 samples, overlay versus raw un-numbered screenshots.
The absolute rates are from the superseded July measurement above and are not
restated here; the ablation is a within-run comparison and the effect is far
too large to be an artefact of the split:

| Challenge | Raw screenshot, relative to the overlay |
|---|---|
| reCAPTCHA 3×3 | drops by roughly a sixth |
| reCAPTCHA 4×4 | **collapses to zero — not one board solved** |

The 4×4 collapse is the tell. A 4×4 is one continuous photograph with sixteen
cells and no separate subjects to anchor on, so with no labels to read the
model invents a convention and answers `1..k` — right number of tiles, wrong
region, mean IoU 0.33. A 3×3 of nine distinct photographs degrades far more
gently, which is exactly why this mistake can hide.

### The prompt is not the same on both sides

`canonical_instruction()` (training/grading) and `planner.SELECT_GRID_PROMPT`
(the shipped client) are byte-identical for reCAPTCHA 4×4 and hCaptcha 3×3
property, but **differ for reCAPTCHA 3×3**: the training prompt carries an
extra paragraph the client omits —

> "Tiles with a blue checkmark badge are ALREADY CLEARED. Do not include them
> in your answer. Tiles that are fading to white (mid-transition) are also
> cleared and must be excluded."

Measured head-to-head on 240 paired reCAPTCHA 3×3 samples, the difference is
not significant: 80.0% (client) vs 78.8% (training), 11 discordant pairs, exact
two-sided sign test **p = 0.55**. That is expected on a static test set, where
no tile is mid-transition. It may still matter in a live dynamic solve, where
cleared tiles fade and are replaced — which is the case the paragraph exists
for and the case this corpus cannot measure.

### Two label bugs in the eval set

Both are in `cleanSamples/test/test_solutions.json`, and both silently corrupt
any harness that trusts the file naively:

1. **Every one of the 288 real reCAPTCHA 4×4 images has a 3×3 instruction.**
   The `instruction` field says `Grid: 3x3 (9 cells)` while the ground truth
   names cells up to 16. `src/testing/grade.py` already sidesteps this —
   `synthesize_instruction()` rebuilds the prompt from
   `canonical_instruction(puzzle_type)` — so any new harness must do the same.
2. **`rows`/`cols` disagree with `puzzle_type` on those same records.** Trust
   `puzzle_type`; it is the only field that agrees with the answer.

**Browser solve rates are a different measurement.** In a live run against
`google.com/recaptcha/api2/demo` on 2026-07-27, Camoufox cleared 3/3 challenges,
taking 5–8 model rounds — reCAPTCHA replaces tiles after every click, and each
replacement is a fresh puzzle. The run came from a datacenter IP, which is the
least favourable condition available; see
[below](#rate-limiting--ip-reputation).

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
> use the **hosted API** instead — it runs the production adapter on our fleet with no GPU of
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
