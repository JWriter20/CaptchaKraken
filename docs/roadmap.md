# 🗺️ Roadmap

Where CaptchaKraken is headed. Legend: 🟢 shipped · 🟡 in progress · ⚪ planned.

## 🟢 Recently shipped

| Item | Status | Notes |
|---|---|---|
| 🛡️ **Stale-frame freshness guard** | 🟢 shipped | Re-checks the frame after inference and re-solves if tiles faded in mid-generation, so we never click a stale answer. See [How it works](./how-it-works.md#freshness-guard-no-acting-on-a-stale-frame). |
| ⬇️ **Unified `fetch` command** | 🟢 shipped | `captchakraken fetch` pulls the latest weights from HF **and** upgrades vLLM in one step. See [Self-hosting → Updating](./self-hosting.md#updating). |
| ♻️ **Per-solve solution dedup** | 🟢 shipped | Byte-identical frames are never re-sent to vLLM. |
| 🧩 **hCaptcha click / drag puzzles** | 🟢 shipped | Full-puzzle model → pixel-space click/drag actions. |

## 🟡 In progress

| Item | Status | Notes |
|---|---|---|
| 🎯 **reCAPTCHA 4×4 robustness** | 🟡 in progress | Our weakest grid type end-to-end — actively improving. |
| 📈 **More real labeled data** | 🟡 in progress | Broader coverage for under-represented prompts. |

## ⚪ Planned

| Item | Status | Notes |
|---|---|---|
| 🎥 **Video challenge support** | ⚪ planned | Solve the video challenges currently detected-and-skipped upstream. |
| 🧩 **More captcha types** | ⚪ planned | Non-grid hCaptcha puzzles: drag-and-drop, path/connect, tetris-fit, and "choose the card". |
| ☁️ **Hosted cloud API** | ⚪ planned | Solve over HTTP, no GPU required. |
| 🪶 **Smaller / faster quantizations** | ⚪ planned | So lower-VRAM hardware can self-host comfortably. |

---

> 📣 **Watch the repo to hear about these as they ship**, and ⭐ star if the
> project is useful to you — it genuinely helps:
> [**CaptchaKraken**](https://github.com/JWriter20/CaptchaKraken). Use GitHub's
> **Watch → All Activity** for release notifications.

---

← Back to [docs index](./README.md)
