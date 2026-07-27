# 🗺️ Roadmap

Where CaptchaKraken is headed. Legend: 🟢 shipped · 🟡 in progress · ⚪ planned.

## 🟢 Recently shipped

| Item | Status | Notes |
|---|---|---|
| 🛡️ **Stale-frame freshness guard** | 🟢 shipped | Re-checks the frame after inference and re-solves if tiles faded in mid-generation, so we never click a stale answer. See [How it works](./how-it-works.md#freshness-guard-no-acting-on-a-stale-frame). |
| ⬇️ **Unified `fetch` command** | 🟢 shipped | `captchakraken fetch` pulls the latest weights from HF **and** upgrades vLLM in one step. See [Self-hosting → Updating](./self-hosting.md#updating). |
| ♻️ **Per-solve solution dedup** | 🟢 shipped | Byte-identical frames are never re-sent to vLLM. |
| ☁️ **Hosted cloud API** | 🟢 shipped | `api.captchakraken.com`. Self-serve signup with GitHub, free credits on the account, metered per inference round. Runs **Abyss**. |
| 🧩 **hCaptcha click / drag puzzles** | 🟢 shipped | Full-puzzle model → pixel-space click/drag actions. |

## 🔴 Broken, being fixed

| Item | Status | Notes |
|---|---|---|
| 🎯 **reCAPTCHA 4×4** | 🔴 **0/80 exact match** | Measured 2026-07-27. The model picks the right *number* of tiles and the wrong ones (mean IoU 0.33), and it is not an indexing bug — see [Performance](./performance.md#recaptcha-4×4-is-broken-and-here-is-the-evidence). A 4×4 is one image cut into tiles, which is a segmentation task, not a "pick the matching photos" task. **First target of the next Abyss run.** |
| 🏷️ **4×4 eval labels** | 🔴 known bad | All 288 real 4×4 records in `test_solutions.json` carry a 3×3 instruction. `grade.py` works around it via `canonical_instruction()`; the file itself still needs fixing. |

## 🟡 In progress

| Item | Status | Notes |
|---|---|---|
| ⬛ **Abyss** | 🟡 in progress | The hosted-only model, trained against the open weights' measured failures. Video challenges land here first. |
| 🪶 **Sunlight / Twilight merges** | 🟡 in progress | The adapter merged into the base at 4-bit and 8-bit, so self-hosting is one download instead of two. Ids reserved, weights not yet uploaded. |
| 📈 **More real labeled data** | 🟡 in progress | Broader coverage for under-represented prompts. |

## ⚪ Planned

| Item | Status | Notes |
|---|---|---|
| 🎥 **Video challenge support** | ⚪ planned | Solve the video challenges currently detected-and-skipped upstream. **Abyss only** — the open weights will keep skipping them. |
| 🧩 **More captcha types** | ⚪ planned | Non-grid hCaptcha puzzles: drag-and-drop, path/connect, tetris-fit, and "choose the card". |

---

> 📣 **Watch the repo to hear about these as they ship**, and ⭐ star if the
> project is useful to you — it genuinely helps:
> [**CaptchaKraken**](https://github.com/JWriter20/CaptchaKraken). Use GitHub's
> **Watch → All Activity** for release notifications.

---

← Back to [docs index](./README.md)
