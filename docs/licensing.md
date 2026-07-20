# ⚖️ Licensing

CaptchaKraken is **source-available** under the **CaptchaKraken Source-Available
License v1.0** — see the full text in [`../LICENSE`](../LICENSE). This page is a
plain-English summary, not legal advice.

> **Build *with* it; don't sell *the solve*.**

Source-available is **not** the same as open-source: the source is public and you
may build on it, but the license adds the restrictions below. It is not
OSI-approved.

## ✅ Allowed

- **Personal use, research, and education.**
- **Commercial use as an internal, enabling component** of a larger product or
  service that delivers substantial value **beyond** captcha solving itself —
  i.e. where the solve is a means to an end your users are actually paying for.
  Illustrative examples:
  - Web scrapers and data-collection pipelines.
  - Stealth / anti-detection browsers and automation frameworks that use the
    solver as one internal capability.
  - QA, testing, and accessibility tooling.

## ⛔ Not allowed (without a separate commercial agreement)

- **Selling captcha-solving as a service** — any product or API whose primary
  value is solving captchas, powered by this software or its model outputs.
- **Thin wrappers** whose main purpose is solving — browser extensions, hosted
  endpoints, SaaS products, or CLIs that simply expose the solving capability to
  end users.
- **Relaying the model's outputs** (bounding boxes, tile selections, click plans)
  through a paid or public API as a captcha-solving service.

Model outputs are covered to the same extent as the software itself.

## Need one of the prohibited uses?

Commercial licensing is available. Open an issue or message the maintainer on
GitHub: [github.com/JWriter20/CaptchaKraken](https://github.com/JWriter20/CaptchaKraken).

## Responsible use

Use responsibly and lawfully — respect the terms of service of any site you
interact with, and applicable anti-fraud and computer-access laws. This project
is for legitimate automation, research, and testing.

---

← Back to [docs index](./README.md)
