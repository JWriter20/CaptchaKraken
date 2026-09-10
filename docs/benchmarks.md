# Benchmarks

Two measurements, because they answer two different questions, and a single
percentage that blurs them is worth nothing to anyone deciding whether to buy.

- **[On real captchas](#on-real-captchas)** — the client driving a real browser
  against the vendors' own public demo pages, end to end, until the vendor
  accepts. This is the number you experience.
- **[On static images](#on-static-images)** — the model alone, answering a
  held-out set of real captured puzzles. This is the number that says which
  *kinds* of puzzle it is good at.

The browser number is always the lower of the two, and always the more honest
one: a correct answer still has to be clicked, in the right place, before the
widget's own timeout, past whatever the vendor thinks of the mouse that moved.

Every figure here is a count over a dated run against a named model. Nothing is
extrapolated and nothing is rounded up.

---

## On real captchas

Each row is one puzzle type driven on the **vendor's own public demo page**
through the hosted API, with every attempt scored. Counts, not percentages: at
these sample sizes a percentage implies a precision the count does not have.

**A row is the whole widget, not one puzzle.** Vendors ask again — hCaptcha
usually wants two rounds, reCAPTCHA keeps going until it is satisfied, and a
later round is often a different shape from the one that opened. A row is named
for the puzzle the vendor *opened* with, and the time covers every round after
it. That is both the honest reading and the stronger claim.

The median is challenge-visible to verified: the span from the puzzle appearing
to the vendor accepting. Page load, the checkbox and the widget's own boot are
the site's latency, not ours, and are excluded.

Measured **2026-08-19** against **CaptchaKraken v1.2 Twilight**, the model the
hosted API serves by default.

| Vendor | Puzzle | Solved | Median |
|---|---|---|---|
| hCaptcha | Image select | 12/12 | 10.5s |
| hCaptcha | Canvas puzzle | 50/50 | 15.1s |
| GeeTest | Ordered icon click | 10/10 | 9.0s |
| GeeTest | Icon crush | 9/10 | 7.1s |
| GeeTest | Gobang | 10/10 | 6.8s |
| GeeTest | 3×3 photo grid | 10/10 | 7.8s |
| GeeTest | Slide jigsaw | 10/10 | 7.6s |
| hCaptcha | Drag puzzle \* | 9/10 | 9.0s |
| reCAPTCHA | 4×4 tile grid | 9/10 | 8.7s |
| reCAPTCHA | 3×3 tile grid | 11/11 | 9.3s |
| reCAPTCHA | 3×3 dynamic | 8/10 | 38.2s |
| GeeTest | Cycling line art | 9/10 | 39.5s |
| hCaptcha | Animated | 36/36 | 45.0s |

**184/189 scored attempts solved.**

\* One row is *supplied*, not scored: a puzzle type we can demonstrate but
have not measured in a scored run. Its figures are asserted by hand. It is
labelled because a hand-written number that looks exactly like a measured one is
the thing most worth labelling.

---

## On static images

The model answering **1,715 real captured puzzles** it has never trained
on, one screenshot at a time, with no browser involved. Scored the way a widget
scores: **exact set match**, pass or fail per puzzle, with the vendor's own
leeway. No partial credit — a partially-correct grid answer is a rejected
captcha.

Every real capture is held out; nothing hand-labelled is trained on. These
measure skill rather than memorisation.

Measured against **CaptchaKraken v1.2 Twilight**. The `n` column is how many
held-out captures of that puzzle we hold — where it is small, read the rate
loosely.

| Vendor | Puzzle | n | Solved |
|---|---|---|---|
| BotDetect | Distorted text | 28 | 96% |
| GeeTest | Canvas puzzle | 74 | 99% |
| GeeTest | Gobang | 26 | 96% |
| GeeTest | Slide jigsaw | 23 | 91% |
| GeeTest | Ordered icon click | 25 | 88% |
| GeeTest | Icon crush | 24 | 83% |
| GeeTest | Nine-tile icon grid | 12 | 83% |
| GeeTest | Slide to fit (v3) | 11 | 73% |
| Lemin | Cropped image | 11 | 100% |
| MTCaptcha | Distorted text | 29 | 97% |
| Prosopo | 3x3 photo grid | 11 | 73% |
| Tencent | Slide jigsaw | 12 | 92% |
| Yandex | Distorted text | 23 | 65% |
| YiDun | Slide jigsaw | 12 | 100% |
| YiDun | Ordered icon click | 12 | 92% |
| YiDun | Picture click | 14 | 79% |
| hCaptcha | Odd shape out | 1 | 100% |
| hCaptcha | Click on the path | 2 | 100% |
| hCaptcha | Different-sized pieces | 1 | 100% |
| hCaptcha | Drag to target | 10 | 100% |
| hCaptcha | Connect images | 5 | 100% |
| hCaptcha | Overlapping lines | 2 | 100% |
| hCaptcha | Rotating object (animated) | 5 | 100% |
| hCaptcha | Arc match | 3 | 100% |
| hCaptcha | Tile flip (animated) | 4 | 100% |
| hCaptcha | Line ends | 23 | 96% |
| hCaptcha | Click past the lines | 16 | 94% |
| hCaptcha | 3x3 property grid | 137 | 92% |
| hCaptcha | Most similar or different | 71 | 86% |
| hCaptcha | Click items in a grid | 84 | 83% |
| hCaptcha | Tower stack (animated) | 12 | 83% |
| hCaptcha | Numbered pieces | 20 | 80% |
| hCaptcha | List selection | 38 | 76% |
| hCaptcha | Click by trait | 87 | 76% |
| hCaptcha | Shape fit | 15 | 73% |
| hCaptcha | Odd animal (animated) | 10 | 70% |
| hCaptcha | Highest jumper | 9 | 67% |
| hCaptcha | Silhouette match | 46 | 65% |
| hCaptcha | Line pieces | 5 | 60% |
| hCaptcha | Highest value (animated) | 14 | 57% |
| hCaptcha | Odd one out (animated) | 2 | 50% |
| hCaptcha | Drag into slot | 83 | 42% |
| hCaptcha | Deviating arrow | 12 | 42% |
| hCaptcha | Connect the path | 58 | 38% |
| hCaptcha | Parking lot | 3 | 33% |
| hCaptcha | Missing piece | 24 | 29% |
| hCaptcha | Spiral gap | 4 | 25% |
| hCaptcha | Growing item (animated) | 8 | 0% |
| reCAPTCHA | 3x3 tile grid | 327 | 71% |
| reCAPTCHA | 4x4 tile grid | 227 | 44% |

**71.8% overall**, weighted by how many captures of each puzzle we hold.

---

## Why the two tables disagree

A puzzle can score well on static images and badly in a browser, and the reasons
are worth naming because a self-hoster will hit them too.

- **An animated puzzle has to be recorded before it can be read.** A still
  screenshot of a cycling board is a picture of one frame, and the answer may
  belong to a frame that has already gone. Those rows carry the longest medians.
- **A drag has to land.** Two boxes correct out of two is a solve; one out of
  two is a rejection. The static score gives partial geometry credit that the
  widget never gives.
- **The vendor gets a vote.** A correct answer clicked by a mouse the vendor
  dislikes is still a failed captcha.

Grids are sent with the cell numbers drawn on, and this is not cosmetic: on raw
un-numbered screenshots the same model scores **0% on 4x4**, because it has to
invent a numbering convention for sixteen cells of one continuous photograph. If
you are building your own client, draw the overlay — see
[performance.md](./performance.md).

---

## Reproducing the browser figures

The example that produced them ships in this repo:

    cd js && npm run demo

It drives the vendors' own demo pages through the hosted API and prints two
clocks per attempt: the solve span, which is what the medians above measure, and
the total, which adds page load and the demo page's own reveal click.

The static-image figures are measured against a held-out corpus of real captures
that is not distributed. The method is stated above in full, so its shape is
reproducible against your own captures.
