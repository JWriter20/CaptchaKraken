# 🗺️ Roadmap

Where CaptchaKraken is headed. Legend: 🟢 shipped · 🟡 in progress · ⚪ planned.

## 🟢 Recently shipped

| Item | Status | Notes |
|---|---|---|
| 🛡️ **Stale-frame freshness guard** | 🟢 shipped | Re-checks the frame after inference and re-solves if tiles faded in mid-generation, so we never click a stale answer. See [How it works](./how-it-works.md#freshness-guard-no-acting-on-a-stale-frame). |
| ⬇️ **Unified `fetch` command** | 🟢 shipped | `captchakraken fetch` pulls the latest weights from HF **and** upgrades vLLM in one step. See [Self-hosting → Updating](./self-hosting.md#updating). |
| ♻️ **Per-solve solution dedup** | 🟢 shipped | Byte-identical frames are never re-sent to vLLM. |
| ☁️ **Hosted cloud API** | 🟢 shipped | `api.captchakraken.com`. Self-serve signup with GitHub, free credits on the account, metered per inference round. Answers with the production adapter — the **Twilight** weights. |
| 🧩 **hCaptcha click / drag puzzles** | 🟢 shipped | Full-puzzle model → pixel-space click/drag actions. |

## 🏷️ Known data issues

| Item | Status | Notes |
|---|---|---|
| **4×4 eval labels** | 🔴 known bad | All 288 real 4×4 records in `test_solutions.json` carry a 3×3 instruction, and their `rows`/`cols` disagree with `puzzle_type`. `grade.py` works around it via `canonical_instruction()`; the file itself still needs fixing. |
| **Train/infer prompt parity** | 🟡 in progress | `canonical_instruction()` and the shipped `SELECT_GRID_PROMPT` differ for reCAPTCHA 3×3 (the already-cleared-tiles paragraph). Measured p = 0.55 on a static corpus, but it is the live-dynamic case the paragraph exists for. |

## 🟡 In progress

| Item | Status | Notes |
|---|---|---|
| ⬛ **Abyss** | 🟡 in progress | The next hosted-only model, trained against the open weights' measured failures. **Not serving yet** — the endpoint answers with the production adapter until it lands. |
| 🪶 **Sunlight / Twilight merges** | 🟢 shipped | The adapter merged into the base at 4-bit (~9 GB) and 8-bit (~14 GB), so self-hosting is one download instead of two. Both public on [HuggingFace](https://huggingface.co/CaptchaKraken). |
| 📈 **More real labeled data** | 🟡 in progress | Broader coverage for under-represented prompts. |
| 🎥 **Video challenge support** | 🟡 in progress | **The driver half has shipped.** A challenge that never settles is now recorded (4 s @ 10 fps), cut into keyframes, and sent to the model as one multi-image prompt; the answer names which keyframe it acted on, and the driver waits for the widget to return to that frame before clicking. What remains is the model: an adapter trained on the keyframe format. It lands with **Abyss**. |

## ⚪ Planned

| Item | Status | Notes |
|---|---|---|
| 🧩 **More captcha types** | ⚪ planned | The non-grid hCaptcha puzzles still unhandled: tetris-fit and "choose the card". Click, drag and path/connect have shipped (above). |

---

> 📣 **Watch the repo to hear about these as they ship**, and ⭐ star if the
> project is useful to you — it genuinely helps:
> [**CaptchaKraken**](https://github.com/JWriter20/CaptchaKraken). Use GitHub's
> **Watch → All Activity** for release notifications.

---

← Back to [docs index](./README.md)
