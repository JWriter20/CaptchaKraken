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

Measured **2026-08-19** against **CaptchaKraken v1.2 Twilight**. Abyss has no
browser figures yet — driving every vendor end to end is a separate run, and a
column filled in from the static table would be a projection wearing a
measurement's clothes. The static table below has both.

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

Both models answering the same real captured puzzles they have never trained
on, one screenshot at a time, with no browser involved. Scored the way a widget
scores: **exact set match**, pass or fail per puzzle, with the vendor's own
leeway. No partial credit — a partially-correct grid answer is a rejected
captcha.

Every real capture is held out; nothing hand-labelled is trained on. These
measure skill rather than memorisation.

The two columns are one run. The gate scores the model under test and carries
the pinned baseline's figure for the same capture beside it, so these are the
same puzzles, the same held-out split and the same solver — which is what makes
subtracting one column from the other mean anything.

The `n` column is how many held-out captures of that puzzle we hold. Where it is
small the rate is one or two puzzles wide: a row at n=2 moving 50 points is one
puzzle changing its mind, not a trend. Read those loosely, and prefer the rows
with three figures behind them.

<!-- BEGIN GENERATED: static-image table -->

Measured **2026-09-11** on **1,436 held-out captures** across **50 puzzle types** — every one we generate, including the ones both models are bad at.

| Vendor | Puzzle | n | CaptchaKraken v1.2 Twilight | CaptchaKraken v1.2 Abyss |
|---|---|---:|---:|---:|
| BotDetect | Distorted text | 28 | 93% | 96% |
| GeeTest v3 | Slide jigsaw (v3) | 11 | 73% | 73% |
| GeeTest v4 | 3x3 photo grid | 12 | 83% | 83% |
| GeeTest v4 | Cycling line art | 74 | 97% | 99% |
| GeeTest v4 | Five-in-a-row board | 26 | 88% | 100% |
| GeeTest v4 | Match-three swap | 24 | 83% | 92% |
| GeeTest v4 | Ordered icon click | 25 | 76% | 80% |
| GeeTest v4 | Slide jigsaw | 23 | 96% | 87% |
| Lemin | Cropped piece | 11 | 100% | 100% |
| MTCaptcha | Distorted text | 29 | 97% | 97% |
| NetEase Yidun | Icon click | 12 | 75% | 92% |
| NetEase Yidun | Picture click | 14 | 71% | 71% |
| NetEase Yidun | Slide jigsaw | 12 | 92% | 100% |
| Prosopo | 3x3 image grid | 11 | 73% | 73% |
| Tencent | Slide | 12 | 92% | 92% |
| Yandex | Distorted text | 23 | 61% | 65% |
| hCaptcha | 3x3 grid by property | 101 | 60% | 92% |
| hCaptcha | Assemble the line | 5 | 40% | 60% |
| hCaptcha | Car in the parking bay | 3 | 67% | 33% |
| hCaptcha | Click along a path | 2 | 50% | 100% |
| hCaptcha | Click items in a grid | 84 | 87% | 93% |
| hCaptcha | Connect the path | 50 | 12% | 48% |
| hCaptcha | Differently sized pieces | 1 | 100% | 100% |
| hCaptcha | Drag into the missing slot | 83 | 29% | 45% |
| hCaptcha | Drag the item onto its target | 10 | 100% | 100% |
| hCaptcha | Find the missing piece | 24 | 33% | 21% |
| hCaptcha | Gap in the spiral | 4 | 0% | 25% |
| hCaptcha | Highest jumper | 9 | 0% | 67% |
| hCaptcha | Highest number (animated) | 14 | 71% | 71% |
| hCaptcha | Items from a list | 38 | 76% | 79% |
| hCaptcha | Line joining two pictures | 5 | 100% | 80% |
| hCaptcha | Match the semicircle | 3 | 67% | 100% |
| hCaptcha | Match the silhouette | 46 | 39% | 57% |
| hCaptcha | Most similar or different | 71 | 76% | 82% |
| hCaptcha | Object crossed by a line | 16 | 25% | 100% |
| hCaptcha | Odd animal out (animated) | 10 | 70% | 70% |
| hCaptcha | Odd shape out, 3D blocks | 1 | 100% | 100% |
| hCaptcha | Order numbered pieces | 20 | 60% | 70% |
| hCaptcha | Overlapping lines | 2 | 100% | 100% |
| hCaptcha | Pick the image by trait | 75 | 57% | 76% |
| hCaptcha | Rotating object (animated) | 5 | 100% | 80% |
| hCaptcha | Stack the tower | 12 | 33% | 83% |
| hCaptcha | Tile flip (animated) | 4 | 100% | 100% |
| hCaptcha | Where the line ends | 23 | 96% | 96% |
| hCaptcha | Which arrow points away | 12 | 33% | 42% |
| hCaptcha | Which item grows | 8 | 0% | 0% |
| hCaptcha | Which one moves differently | 2 | 0% | 50% |
| hCaptcha | Which piece fits | 15 | 60% | 47% |
| reCAPTCHA | 3x3 tile grid | 281 | 68% | 67% |
| reCAPTCHA | 4x4 tile grid | 50 | 44% | 42% |

**65.3% → 74.1% overall**, weighted by how many captures of each puzzle we hold (+8.8% absolute, +14% relative).

Abyss is ahead by ten points or more on **15** of the 50 types and behind by five or more on **6**. Both counts are here because a table that only showed the wins would not be a measurement.

<!-- END GENERATED: static-image table -->

---

## Which model the hosted API serves

The current client asks for **Abyss** by name and is served it. A client old
enough not to name it, and any request that names no model at all, is served
**Twilight**. Abyss is not downloadable — its weights are hosted only, and that
is not a release schedule. Twilight's are public and free to self-host. See
[licensing.md](./licensing.md).

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
