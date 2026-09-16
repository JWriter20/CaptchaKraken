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

Both models, driven the same way on the same pages, so the two columns can be
subtracted. Neither column is filled in from the static table below: a browser
figure projected from a static one is a projection wearing a measurement's
clothes, and the whole reason this table exists is that the two disagree.

The vendors do not deal the same puzzle to both runs on demand, so where one
model drove a puzzle the other never met, the row prints with an em dash rather
than being dropped. An incomplete measurement stays visibly incomplete.

<!-- BEGIN GENERATED: real-captcha table -->

Measured **2026-09-15** through the hosted API, every attempt scored.

| Vendor | Puzzle | Twilight solved | Twilight median | Abyss solved | Abyss median |
|---|---|---:|---:|---:|---:|
| GeeTest | 3×3 photo grid | 6/6 | 6.6s | 6/6 | 6.9s |
| GeeTest | Cycling line art | 6/6 | 23.6s | 5/6 | 18.0s |
| GeeTest | Gobang | 6/6 | 6.7s | 6/6 | 6.4s |
| GeeTest | Icon crush | 3/6 | 12.7s | 4/6 | 6.1s |
| GeeTest | Ordered icon click | 5/6 | 9.8s | 6/6 | 13.2s |
| GeeTest | Slide jigsaw | 6/6 | 7.1s | 6/6 | 6.4s |
| hCaptcha | Animated * | 0/9 | — | 5/8 | 25.8s |
| hCaptcha | Canvas puzzle * | 10/15 | 26.9s | 7/8 | 33.9s |
| hCaptcha | Image select * | 6/15 | 26.6s | 8/8 | 7.5s |
| reCAPTCHA | 3×3 dynamic | 4/8 | 29.8s | 8/8 | 19.2s |
| reCAPTCHA | 3×3 tile grid | 9/10 | 10.6s | 12/12 | 9.7s |
| reCAPTCHA | 4×4 tile grid | 10/10 | 12.6s | 9/9 | 9.5s |

 \* **Two measurements that share a name, not a comparison.** hCaptcha picks the challenge itself, so each of these rows is a SHAPE holding several different puzzles, and which ones landed in it differs between the two runs. The animated row is the extreme case: the puzzles behind it score anywhere from 0% to 100% on the static table below, so at these counts it records which variants the vendor dealt at least as much as it records the model. Subtract these columns and you will be measuring the vendor's shuffle.

**Twilight: 71/103 scored attempts solved**, over 12 puzzle types.

**Abyss: 82/89 scored attempts solved**, over 12 puzzle types.

**Over the 9 rows that are the same puzzle in both runs: Twilight 55/64 against Abyss 62/65.** The vendors behind these deal one puzzle per target, so the two columns answered the same question. The totals above include the marked rows and therefore cannot be subtracted; this line can.

<!-- END GENERATED: real-captcha table -->

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

| Vendor | Puzzle | n | Twilight | Abyss | Twilight, est. widget | Abyss, est. widget |
|---|---|---:|---:|---:|---:|---:|
| BotDetect | Distorted text | 28 | 93% | 96% | — | — |
| GeeTest v3 | Slide jigsaw (v3) | 11 | 73% | 73% | — | — |
| GeeTest v4 | 3x3 photo grid | 12 | 83% | 83% | — | — |
| GeeTest v4 | Cycling line art | 74 | 97% | 99% | — | — |
| GeeTest v4 | Five-in-a-row board | 26 | 88% | 100% | — | — |
| GeeTest v4 | Match-three swap | 24 | 83% | 92% | — | — |
| GeeTest v4 | Ordered icon click | 25 | 76% | 80% | — | — |
| GeeTest v4 | Slide jigsaw | 23 | 96% | 87% | — | — |
| Lemin | Cropped piece | 11 | 100% | 100% | — | — |
| MTCaptcha | Distorted text | 29 | 97% | 97% | — | — |
| NetEase Yidun | Icon click | 12 | 75% | 92% | — | — |
| NetEase Yidun | Picture click | 14 | 71% | 71% | — | — |
| NetEase Yidun | Slide jigsaw | 12 | 92% | 100% | — | — |
| Prosopo | 3x3 image grid | 11 | 73% | 73% | 92% * | 92% * |
| Tencent | Slide | 12 | 92% | 92% | — | — |
| Yandex | Distorted text | 23 | 61% | 65% | — | — |
| hCaptcha | 3x3 grid by property | 101 | 60% | 92% | 87% * | 99% * |
| hCaptcha | Assemble the line | 5 | 40% | 60% | 58% * | 85% * |
| hCaptcha | Car in the parking bay | 3 | 67% | 33% | — | — |
| hCaptcha | Click along a path | 2 | 50% | 100% | — | — |
| hCaptcha | Click items in a grid | 84 | 87% | 93% | 99% * | 99% * |
| hCaptcha | Connect the path | 50 | 12% | 48% | 7% * | 69% * |
| hCaptcha | Differently sized pieces | 1 | 100% | 100% | — | — |
| hCaptcha | Drag into the missing slot | 83 | 29% | 45% | 33% * | 63% * |
| hCaptcha | Drag the item onto its target | 10 | 100% | 100% | 99% * | 99% * |
| hCaptcha | Find the missing piece | 24 | 33% | 21% | 43% * | 20% * |
| hCaptcha | Gap in the spiral | 4 | 0% | 25% | — | — |
| hCaptcha | Highest jumper | 9 | 0% | 67% | 1% * | 92% * |
| hCaptcha | Highest number (animated) | 14 | 71% | 71% | 95% * | 95% * |
| hCaptcha | Items from a list | 38 | 76% | 79% | 98% * | 99% * |
| hCaptcha | Line joining two pictures | 5 | 100% | 80% | 99% * | 98% * |
| hCaptcha | Match the semicircle | 3 | 67% | 100% | — | — |
| hCaptcha | Match the silhouette | 46 | 39% | 57% | 53% * | 82% * |
| hCaptcha | Most similar or different | 71 | 76% | 82% | 98% * | 99% * |
| hCaptcha | Object crossed by a line | 16 | 25% | 100% | 28% * | 99% * |
| hCaptcha | Odd animal out (animated) | 10 | 70% | 70% | 94% * | 94% * |
| hCaptcha | Odd shape out, 3D blocks | 1 | 100% | 100% | — | — |
| hCaptcha | Order numbered pieces | 20 | 60% | 70% | 86% * | 95% * |
| hCaptcha | Overlapping lines | 2 | 100% | 100% | — | — |
| hCaptcha | Pick the image by trait | 75 | 57% | 76% | 83% * | 98% * |
| hCaptcha | Rotating object (animated) | 5 | 100% | 80% | 99% * | 98% * |
| hCaptcha | Stack the tower | 12 | 33% | 83% | 44% * | 99% * |
| hCaptcha | Tile flip (animated) | 4 | 100% | 100% | — | — |
| hCaptcha | Where the line ends | 23 | 96% | 96% | 99% * | 99% * |
| hCaptcha | Which arrow points away | 12 | 33% | 42% | 44% * | 59% * |
| hCaptcha | Which item grows | 8 | 0% | 0% | 1% * | 1% * |
| hCaptcha | Which one moves differently | 2 | 0% | 50% | — | — |
| hCaptcha | Which piece fits | 15 | 60% | 47% | 86% * | 67% * |
| reCAPTCHA | 3x3 tile grid | 281 | 68% | 67% | — | — |
| reCAPTCHA | 4x4 tile grid | 50 | 44% | 42% | — | — |

**One board, one answer: 65.3% → 74.1%** (+8.8% absolute, +14% relative), weighted by how many captures of each puzzle we hold.

**Estimated chance of clearing the whole widget: 70.9% → 86.5%** (+15.6% absolute), over the 24 types we cannot summon on demand. An ESTIMATE on both sides — nothing in this column was driven. What we drove is the real-captcha table above.

The two widget columns are **estimates, not measurements**, and every cell in them carries the mark for that reason. Most hCaptcha puzzle types cannot be summoned on demand, so the estimate applies that vendor's **measured** leniency and **measured** board allowance to this type's one-shot rate. A blank is a type whose vendor allowance we have not measured — unmeasured, not zero. Types we DID drive are deliberately absent here and present in the real-captcha table above, with the counts behind them: a browser rate and a projection are different quantities, and one column cannot hold both.

These estimate the **whole widget**, the same unit the real-captcha table uses — not one board. That is why a figure here can sit BELOW the one-shot beside it: hCaptcha usually asks for two boards in a row and both have to land, so a type at 12% a board clears the widget less often than 12% of the time, however many retries it is given. Where a vendor deals one board, the retries can only push it up.

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
