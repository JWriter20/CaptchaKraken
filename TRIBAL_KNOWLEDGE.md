# TRIBAL_KNOWLEDGE.md

Decisions taken in this repository, and why. Not a changelog and not a design
doc — the things a new contributor would otherwise learn by breaking something.

---

## Layout and publishing

**One repository, three published packages.** `js/` is the browser driver on
npm, `python/` is the engine and CLI on PyPI, and `mcp/` is the account server on
npm. They ship together because the two solver ports must behave identically:
`contract.json` records both public surfaces and a new divergence fails the
build.

**The JS driver bundles the Python engine instead of reimplementing it.**
`js/scripts/copy-python.mjs` copies repo-root `python/` into `js/python/` at
build time, and the driver shells out to that CLI for grid detection and
prompting. npm can only pack files inside the package root, so the copy exists
purely to satisfy packing — the single source of truth is the repo-root
directory, and `js/python/` is gitignored.

**The MCP server is deliberately not a dependency of the solver, and has its own
version line.** Signing in and minting a key is a one-time onboarding path; the
solver client has a handful of runtime dependencies and should not pull the MCP
SDK and a schema library on every browser-driver install. Tying the version
numbers together would also mean cutting an MCP release every time the solver
changed.

**Each published package carries its own `AGENTS.md`.** Whoever runs `npm i`
gets `js/` and nothing above it; whoever runs `pip install` gets the package
directory. `js/package.json` lists the file under `files`; the wheel
force-includes `python/AGENTS.md` because it sits beside `pyproject.toml` rather
than inside `src/captchakraken/`, and a wheel carries only the package.

**`AGENTS.md` is the product's documentation; `CONTRIBUTING.md` is the
contributor's.** Other people install this repo, so a consumer's agent loads
`AGENTS.md` from inside *their* project, where "branch off `dev`" and "delete
dead code when you find it" read as instructions about code that is not ours.
`CLAUDE.md`, `.cursorrules` and `GEMINI.md` therefore point at
`CONTRIBUTING.md`, which is also where the ecosystem already looks and where
GitHub surfaces it on every PR.

## Branches, gates and releases

**`main` only ever takes a merge from `dev`, enforced by a workflow rather than a
setting.** Branch protection can require a PR and require checks, but it has no
rule for which branch may be the *source* of a merge — so the one property that
describes this project's release flow is the one GitHub cannot express, and a
required check is the only place to put it. There is no bypass inside
`promote.yml` on purpose: a check with its own escape hatch is a check that gets
escaped.

**A push to `main` publishes, so `main` is the most irreversible branch here.**
`publish.yml` fires on push and puts both ports on npm and PyPI within minutes,
at a version number that can never be reused. There is no rollback for a
publish, only a higher version.

**Publishing is tokenless, via OIDC trusted publishing, and idempotent.** No
registry secret lives in this repo to leak or rotate, and a version already on
the registry is skipped rather than failed, so re-running a half-finished release
is safe. The one exception is a brand-new npm package: trusted-publisher settings
live on the package page, so the very first publish of a new name needs a one-off
manual push before the automated path works.

**CI runs on pull requests into `dev` as well as into `main`.** It did not, and
work therefore landed on the integration branch ungated and first met CI at the
`dev → main` promotion — where a failure blocks the *release* instead of the
change that caused it. The jobs are hermetic and Actions minutes are free on a
public repo, so there was never a cost argument for the narrower trigger.

**The Python suite runs whole, not from an allowlist.** A hand-picked list of
four files silently skipped tests that existed, passed locally and gated nothing.
If a test is too slow or not hermetic for CI, mark it skipped so the skip appears
in the run output rather than being invisible in a workflow file.

**A pull request here is gated on driving, not on solve rate.** This repo ships
the driver, so a PR cannot change the model; what it *can* break is a selector, a
click landing off-widget, or one port asking for something the other does not.
The fixtures are not in this repo, so `tier3-request.yml` asks the repository
named by the `GATE_REPO` variable to run that gate against the PR's exact commit
and post back a redacted result — a commit status and per-port, per-vendor pass
rates, and nothing else.

**That dispatch uses the REST endpoint directly, and the gate repository's
address lives in a variable.** `gh workflow run` looks a repository's default
branch up first, purely to pick a ref for you, which costs a metadata permission
the dispatch itself does not need. A public workflow file is also not the place
to write down where a private repository lives.

**A fork's PR cannot run that gate, and that is the design.** `pull_request`
never exposes secrets to a fork's own workflow run, so no dispatch token can
reach untrusted code; the check simply sits pending, and a maintainer re-runs it
by pushing the branch into this repo.

## The model contract

**Prompts belong to the model, not to a client release.** A model answers in the
schema it was trained on, and when the shipped prompt drifts from it nothing
errors — puzzles just fail silently. So `models.json` records which prompt
generation every published model was trained on, and `prompts.py` ships the
built-ins for every generation still in service; hardcoding one generation makes
drift inevitable, because updating the constants breaks already-published models
and not updating them breaks the new one.

**`keyframes.py` is a verbatim port and must not be improved here.** The model is
trained on keyframes cut by the training-side extractor and answers with a frame
*number* indexing into them, so if this copy sliced a recording differently the
number would name a picture that does not exist and the driver would wait for a
state the page never reaches. Its region-diff metric is reused as the driver's
wait-for-state gate, so the two agree by construction.

**Grid screenshots are sent with cell numbers drawn on them.** The client runs
`find_grid`, has `overlay.py` render the numbered labels, and sends that image,
because the model reads those labels and was never trained to invent a numbering.
Skip the overlay and reCAPTCHA 4×4 scores zero without raising anything — it
looks exactly like a broken model.

**`find_grid` is pure OpenCV, with no model in it, and is the most heavily gated
code in the repo.** Every grid solve rests on it, and each geometry gate exists
because a specific false positive happened: a lattice whose gutters separated
nothing, a shape no vendor ships, a footer counted as a row. Read the test named
for the gate before changing one.

**One page carries every published number.** `docs/benchmarks.md` holds the
end-to-end browser counts and the static-image rates, deliberately as counts
rather than percentages, because the two measurements answer different questions
and a single blended percentage answers neither. Nothing anywhere else in the
repo may derive, round or convert one of them.

**The version is one number in three places.** `python/pyproject.toml`,
`js/package.json` and `captchakraken.__version__` are compared by a test because
they drifted once: the runtime attribute said one release while the wheel on PyPI
said another, so every caller branching on it — and every bug report quoting it —
named a version that had not been current for weeks.

## Client details that cost a real diagnosis

**`python3` is tried before `python`.** On Debian-family systems there is no
`python` on `PATH` at all, so every JS solve died with "python: not found" before
it ever reached the model — and because the Python client obviously ran, it
looked like an endpoint or model problem rather than a missing binary.

**The package defines its own structural `Page` type instead of importing a
browser's.** That is what lets the driver accept any Playwright-compatible
launcher and install no browser of its own; a real dependency would pick the
launcher for the user.

**Tests are selected by a tsconfig, not by a list of filenames.** The old `npm
test` named every test and every module it imported, so adding a test meant
editing that list and a test left out of it silently never ran — which had
already happened.

**A puzzle-piece slider is aimed once and then corrected, not calibrated
first.** The distance between the piece and the slot is already an estimate of
how far the handle has to travel, so the drag opens with one sweep at the gap —
the gesture a person makes, and one move and one screenshot cheaper than the two
measurement nudges it replaces. What the nudges were really for is the piece's
width and the handle-to-piece ratio, and the sweep supplies both better: it
carries the piece clear of the ground it vacated, leaving two separate marks in
the frame, and the right-hand one IS the piece. Measuring it beats inferring it
from the union of the two, whose width is the piece plus a travel that is only
believed.

**The piece does not always start under the handle, and a sweep aimed from the
handle can push it off the board.** One vendor's handle sits 42px into a 360px
card while its piece sits 136px in, so aiming the handle at a slot 288px across
sends the piece to 382 — past the edge, where the only thing left to photograph
is the ground it vacated. `locate_piece` reports that for what it is, a mark
narrower than the distance travelled, and says where the piece went; the next
correction brings it back. Reported instead as "nothing moved", it is
unrecoverable: the loop looks again, sees the same thing, and lets go with the
piece off the board.

**Nothing may be written to stdout by the MCP server.** On the stdio transport
stdout *is* the protocol channel, so one stray `console.log` — a banner, a
deprecation notice — corrupts the stream and the client reports the server as
broken rather than as chatty. Diagnostics go to stderr, which clients surface in
their logs.

**A minted API key is written straight to a `0600` file and never returned
through the conversation.** The obvious move for an agent is to echo the key it
just created, and a transcript is not a secret store. `create_api_key` reports
only the path, and the solver reads that file with no environment variable set.

## Navigation, coverage and pins

**There are two maps, and only one of them is committed.**
`FILE_PURPOSES.md` answers "where does X live?" and is tracked, reviewed and
gated. `repomix-output.md` answers "does a helper for this already exist?", is
generated by `npm run repomix` in well under a second, and is gitignored — a
generated map that is committed goes stale and still gets trusted, which is worse
than not having one.

**The map is uncompressed, against the letter of the spec.** repomix's
`compress` mode keeps classes, functions and interfaces but drops plain
declarations: with it on, `js/src/limits.ts` came out as an empty code block, and
`grep -nE "const foo ="` — the search the rule actually tells you to run — found
nothing. Comments are still stripped. The map is 519 KB instead of 171 KB, which
costs nothing for a file nobody commits.

**The root `package.json` is tooling, and says so loudly.** repomix and the
codebase-map gate need a manifest, and putting them in `js/` would ship a
contributor's tools to every consumer of the published package. So the root
manifest is `private: true`, is published nowhere, is named in
`FILE_PURPOSES.md` as tooling-only, and is the first thing `CONTRIBUTING.md`
explains about the layout — because three manifests in one repo is already
enough ambiguity about which one `pip install` and `npm i` actually use.

**`FILE_PURPOSES.md` is now gated, in both directions, before any test runs.**
`.github/scripts/check-file-purposes.mjs` compares `git ls-files` to the table
rows and fails on a tracked file with no entry, an entry naming a path that is
gone, and a path listed twice. It has no dependencies so it can run before
anything is installed, and every test job waits on it: a missing row is one line
to fix, and discovering that twenty minutes into a test run is a waste nobody
chose.

**Coverage floors are set at what the suites measure, not at what the spec
asks.** The spec wants 90% line and branch on a service anything external
depends on. Python measures **58.31%** combined line-and-branch on 3.12 and
58.33% on 3.10 — **31.69 points short** of 90. The TypeScript driver measures **71.10%
line and 78.48% branch** — **18.90 and 11.52 points short**. The floors are
therefore 58 and 71/78: the measured values rounded down. A gate that is red the
day it lands blocks every pull request and teaches the team to bypass gates,
which costs more than the gap it advertises. The numbers go up, never down, and
the distance above is the number to close.

**The Python floor is measured on the interpreters CI actually runs.** 3.10
reports 58.33% and 3.12 reports 58.31% — 0.02 of a point apart — so the
threshold runs on both legs rather than on one measured leg and one guess. The
first version of this was measured on 3.14, the only interpreter on the machine
at the time, and that habit is what produced the numpy break below; a real 3.10
environment (`uv python install 3.10`) costs two seconds.

**c8 and `node:test`, not Vitest and `@vitest/coverage-v8`.** The spec names
Vitest, and switching would mean rewriting 30 test files that currently import
`node:test` — a change to the tests themselves, which is not something a
coverage gate is allowed to smuggle in. c8 reads the same V8 coverage Vitest
would and emits the same lcov. The `sourceMap` flag in `tsconfig.test.json`
exists only so those numbers land on `src/*.ts` rather than on compiled output;
`dist/` is built by `tsconfig.json` and carries no maps.

**c8 runs without `--all`, and the new tests did not change that.** With `--all`
pointed at a TypeScript source tree every file still reports 0% — the synthesised
entries collide with the source-mapped ones, which is a c8 limitation rather than
a function of how much is tested. Without it a module no test imports would be
invisible, and the reason that is survivable is checkable rather than assumed:
comparing the report's file list against `src/*.ts` leaves exactly one file out,
`playwright-types.ts`, which is types only and emits no executable line. Re-run
that comparison when adding a module; do not reach for `--all`.

**`mcp/` has no coverage number because it has no test suite.** Its gates are a
type-check, a build, and a real MCP handshake against the built binary. A
percentage measured over a smoke test would describe nothing, and inventing one
to fill the column is worse than leaving the column empty.

**Codecov uploads are gated on the token; the thresholds are not.**
`CODECOV_TOKEN` does not exist in this repo yet, and on a fork PR it never will —
`pull_request` does not expose secrets to a fork's own run, the same reason the
driver gate cannot run there. So the upload step skips when the token is empty
while `fail_under` and `check-coverage` run unconditionally. A required check
that passes by not running is worse than no check at all.

**Every version is exact, including CI actions.** All four manifests pin `==` /
no-caret versions taken from what actually resolves and passes, and every GitHub
Action is pinned to a commit SHA with its release tag in a trailing comment —
`actions/checkout@…` was `@v4`, a moving tag that silently changes what runs on
every PR. A version changes through a reviewed PR or not at all.

**A pin must install on the floor the package promises, not on the interpreter
that pinned it.** `numpy==2.5.3` was chosen from a 3.14 environment and turned
the 3.10 leg red on arrival: numpy 2.5.x declares `Requires-Python >=3.12`, so
pip could not find any candidate at all. The fix is 2.2.6, the newest numpy that
still covers 3.10 — not raising `requires-python`, which would drop a platform
installed users are already promised and is a breaking change, not a pinning
detail. Every other pin was re-checked against 3.10 the same way, and the full
suite now runs green on real 3.10.20 and 3.12.13 interpreters before the pins
are believed.

**The map tool is pinned above the repo's own Node floor, deliberately.** The
packages and CI run Node 20; `repomix` needs 22 from 1.14.1 onward. Every
repomix that runs on Node 20 is covered by an unpatched command-injection
advisory, so the root tooling manifest declares `engines.node: ">=22"` and keeps
the patched version. It is a contributor tool, published nowhere and never run
in CI, so the cost is a clear `EBADENGINE` warning rather than a broken build —
and that is a better trade than shipping a known RCE to save a Node upgrade.

**The Python matrix does not fail fast.** A 3.10 failure used to cancel the 3.12
leg mid-install, so a run that should have reported "3.10 cannot resolve this
pin, 3.12 is fine" reported only the first half and left the second unknown. The
legs are minutes long and Actions minutes are free on a public repo; the
information is worth more than the cancelled runner time.

**The `serve` extra is pinned to vllm's own numbers, and cannot be verified
here.** `vllm==0.29.0` declares `torch==2.13.0`, so that pair cannot disagree.
Neither the pins nor the ranges they replaced resolve on a CPU-only box running
Python 3.14 — checked both ways — because the CUDA kernel packages have no wheel
for it, so this pin is no more fragile than what it replaced. Confirm it on a
machine with a GPU before the next release: `pip install --dry-run
"captchakraken[serve]"`.

## Residual, and deliberately not fixed here

**`python/Dockerfile` bases on a torch 2.7.0 image while the `serve` extra
installs 2.13.0 over it.** The old range did the same thing, so this is not new,
but pinning made it legible. Aligning them is an image change with a real GPU
test behind it, not a docs pass.

**The two v1-architecture test files are gone, not skipped.** They imported a
module that is not in this repo, so they could never run under any condition; a
visible `importorskip` was an improvement on silence but it was still a green
check over nothing. Deleted rather than ported: the v1 planner they exercise no
longer exists here.

**The grid-detection corpus of real captures is not in this repo.** The tests
that measure detection rates over it skip when the directory is absent, which is
the case in every clean checkout, so the hermetic gate covers the geometry rules
rather than the rates. It is also part of why the Python floor is where it is.

**Dead code was carrying part of the coverage gap.** Removing four unreferenced
`find_grid` tracer helpers, three unreferenced overlay drawing functions, a
prompt builder that duplicated `prompts.py` while hardcoding the newest
generation, a leftover `promisify(exec)` in the TypeScript solver, and nine
unused imports moved Python coverage from 53.45% to 54.74% without a single new
test — those lines were uncovered because nothing called them. Two rules made it
safe: an exported symbol is published API and was never a candidate, and every
deletion had to be unreferenced across `git ls-files` AND across the installed
consumers on this machine.

**`planner.VIDEO_ACTION_PROMPT_TEMPLATE` is unreferenced and was kept.** Its two
siblings, `SELECT_GRID_PROMPT` and `PIXEL_ACTION_PROMPT`, are hashed by
`pinned_model.json` and asserted by `test_pinned_model.py`, so the three are one
declared set rather than three loose constants — and the comment above it is the
only explanation of the animated pipeline in that module. The function that used
it was the duplicate worth deleting; the alias is one line.

**Test output is kept out of the published package by the bundler, not by the
test.** `python/tests/test_solver.py` writes debug PNGs to `latestDebugRun/`
relative to the working directory, and `js/scripts/copy-python.mjs` whitelists
`python/` wholesale into the npm tarball — so before this was fixed, running the
suite and then building would have published a coverage dump and a folder of
debug images. Both are now skipped by the bundler and gitignored; the test still
writes into the tree, which is where it should be fixed.

**Three advisories against the MCP SDK's transitives are left open, because the
vulnerable code is never loaded.** `fast-uri`, `hono` and `qs` arrive under
`@modelcontextprotocol/sdk@1.30.0`, which is the latest release, so there is no
upgrade to take. They reach us only through the SDK's HTTP transport; `mcp/src`
imports `StdioServerTransport` and nothing else, so that transport is never
constructed and those packages are never on a code path this server executes.
We deliberately did not add npm `overrides`: forcing a transitive to a version
its parent did not choose is an upgrade rather than a pin, and this is the one
package with no test suite to catch what it breaks. Re-check when the SDK
releases.

**A grid is a lattice, and the detector was only ever asked what was inside the
cells.** `find_grid` traces separator lines "of any border colour and small
tilt" and recovers the slant on its own, so a TILTED lattice is one of its
legitimate outputs. No vendor ships one — every grid we solve is laid out with
CSS, on the pixel — so that tolerance is headroom no true board needs and a
photographic backdrop's own edges walk straight through it. `_is_real_grid`
then passed the result, because every check it had asks about cell CONTENT
(colour spread, the shared-background test that tells a sprite board from
tiles) and a click puzzle drawn over a photograph answers all of them honestly.
A grid answer is a LIST OF CELLS, so the mis-route does not read as a wrong
answer; it reads as the driver pressing six things on a board whose answer is
one press. The shape check now runs first and costs nothing — it needs no
pixels. Measured over the real captures, six per family: every true grid the
detector finds is regular to the pixel (25 detections, all 0.000) against 0.128
for the false one, so the bar is a floor with a 2x margin rather than a tuned
number. It is not zero only because an antialiased separator traced on a 100px
cell can honestly land a pixel out.

**Filming while the model is asked is only free if the camera stops first.**
The speculative burst reads the still and records the widget at once, and the
claim that makes that free is that the recording happens inside a wait the solve
was making anyway. It did not: the settled exit waited on the inference as well
as on the board, so once a board had shown one screen and held it for a full
floor window — nothing further to learn — the loop went on screenshotting at
10 fps for as long as the model took. Measured on a still GeeTest 3x3 photo grid
that broke the 20 s board ceiling: one distinct frame in 120, and the burst
still ran its whole 12 000 ms because that one model call took 22.6 s. The JS
port never had this — its burst breaks on the settled window alone — so the same
board reported `burst 1.5s` + `inference 1.8s` there and `burst 12.1s` plus a
10.4 s hole here. The hole was the second half of the bug: the post-burst wait
for the answer sat outside every phase, so the largest cost on a slow board was
attributed to nothing at all and the report read as "the burst is slow".

**The burst's windows are milliseconds, and were counted in frames.** "A burst
must outlast one full cycle" is a claim about SECONDS: the floor is what makes
the still verdict sound, because it is longer than the longest dwell a cycling
board holds a screen for (max 2.7 s measured on the GeeTest svg board). Both
loops expressed it as `video_burst_duration_ms / (1000 / fps)` frames instead,
and the two only mean the same thing while the loop reaches `video_burst_fps` —
which it does not when a screenshot costs more than the interval. There is no
frame-dropping: the loop sleeps `interval - elapsed`, so a slow camera simply
runs long, and the window stretches by exactly the ratio.

Measured on one element screenshot, same fixture, same box: 15.8 ms through the
desktop browser against 183.5 ms through Chromium at a phone's device pixel
ratio of 2.625. It is the DPR and nothing else — the same viewport and touch
emulation at DPR 1 costs 66.6 ms — so a 40-frame floor meant 4.02 s on one
driver and 8.25 s on the other, for identical evidence, while the inference it
overlapped had finished at 2.15 s. The same ratio scaled the 12 000 ms ceiling
to 20-31 s, which is a whole solve budget on a board that never moved.

The frame count was wrong in both directions, and the regression tests pin
both: a fast camera reached the floor's frame count in 400 ms of a 500 ms
window, so it also cut the clip SHORT of the dwell the window exists to
outlast.

`burst_hang_deadline_ms` keeps its 3x margin. It stopped being a budget for a
slow loop the moment the loop bounded itself in wall-clock, and is now only
what it says it is: headroom for a single screenshot that never returns.

**The rate a burst reports is the rate it achieved.** The slicer dates frames
by it, and a clip logged as "40 frames at 10fps" that actually ran at 5.4 is
what kept the stretched window out of every log and every phase report. It
moves no frames — the slicer picks by index and uses the rate only for
timestamps — so this is honesty in the manifest, not a change of behaviour.

**Re-asking the same pictures is arithmetic, not a retry.** A refused animated
answer drops the ANSWER and keeps the FRAMES, which is right for a cycling
board: the frames do not change, the answer merely landed on the wrong screen.
It is wrong for a clip the slicer could not find a steady screen in.
`steady_screens` counts the screens the clip can be PROVEN to come back to, and
zero means a continuous animation — the keyframes are arbitrary slices of
something that never holds still, the frame number refers to nothing, and the
same question about the same pictures returns the same answer until the solve
dies on `max_no_progress_rounds`. Resampling does not rescue it: a temperature
moves the coordinate a little, not the reading. Those clips are now thrown away
on refusal, which is what lets the next round re-classify a board that has
stopped moving as the still it now is.

**The piece selector list is a second vendor surface, and it fails silently.**
`VENDOR_WIDGET_LOCATORS` failing is loud — nothing is detected. The piece list
failing is not: the driver falls back to measuring what moved between two
screenshots and solves anyway, so the pass rate does not move and no gate can
see it. Re-measured against the live vendor demos 2026-09-13 and it had two
defects. Tencent's piece carries a real, stable, vendor-named class that matches
none of the generic patterns, so every live Tencent slide had been taking the
fallback. And on Yidun the generic `[class*="jigsaw"]` matches TWO elements, the
first in document order being the widget's own container — five times too wide
and never moving — so "first visible match" would have handed the correction
loop a number that cannot change. The lookup now takes EVERY match of each
selector and keeps the first that could BE a piece, using the same size bound
the geometry path already throws a measurement out on. GeeTest has also started
stamping a per-build hash alongside the plain class; the plain one still matches
today, and the day it stops is the day the list goes blind, which is what the
live check is for.

**The mouse path is not ours and should not be.** It was a Bezier arc with a
Fitts's-law duration, an ease-in-out velocity profile, speed-scaled jitter and
an overshoot-and-correct. Every one of those is a MODEL of what a hand does,
tuned by hand, and every one is a closed form — which is exactly what makes it
findable. A detector does not need to know our constants; it needs to know that
the path is drawn from a two-parameter family at all. `cursory` models nothing:
it searches a database of thousands of trajectories recorded from real people
for the closest match to the requested movement, morphs that recording onto the
endpoints, and re-noises it. The realism is measured rather than asserted, and
the worst case is a bad recording rather than a recognisable curve.

IT ALSO SETTLES THE DUAL-PORT PROBLEM FOR GOOD. Every other shared surface is
two implementations and a test that they agree. The mouse was one algorithm
written twice and pinned STATISTICALLY, because that is the best two
independent implementations can do: a curve written twice differs in the last
bits and then diverges over a thousand samples. `cursory-js` is a port rather
than a rewrite — both reduce to the same numpy PCG64 stream — so a seed gives
the same trajectory in both, and the parity is now pinned exactly, timings
included. Measured: seed 42 over (120, 80) -> (940, 560) is 48 identical points
in both, coordinates agreeing to about 2e-12 px (V8 and CPython rounding `exp`,
`atan2`, `sin`, `cos` differently) and timings equal outright.

WHAT IT COST, AND WHAT IT DID NOT. Move durations track the old model at the
median (193 vs 197 ms over 30 px, 681 vs 708 over 1000) with a much longer tail
— max 1354 ms against 819 — which is what real people look like and is the
thing to watch against the per-board clock. Import is ~60 ms and the first call
5 ms, against a numpy the client already loads for OpenCV, so the per-solve cost
is noise. The TOUCH model stays ours: Cursory records mice, and a finger is not
a slower mouse — different velocity profile, different bow, a contact patch that
wanders.

THE LICENCE IS THE PART TO BE CAREFUL WITH. Both packages are
LGPL-3.0-or-later and this product is source-available proprietary. That
combination is fine, and it is fine BECAUSE they are ordinary installed
dependencies: declared in `pyproject.toml` and `package.json`, resolved by the
user's package manager, never vendored, inlined, or bundled. `tsc` leaves
`require('cursory-js')` as a runtime import rather than inlining it, and
`copy-python.mjs` copies only our own tree. Vendoring either one is the change
that would move this obligation somewhere else, so do not, and see NOTICE.

TWO PACKAGING CONSEQUENCES, both deliberate and both part of the major bump.
The Python client's `numpy` pin moves from 2.2.6 to 2.3.5, because Cursory
requires `numpy~=2.3.3` and the old pin was OURS — opencv asks only for
`numpy>=2`. That conflicts with `numba`, which arrives only under the `[serve]`
extra (vLLM self-hosting) and is never imported by the solver. And the JS client
gains its FIRST runtime dependency; it had none, which was a real property of a
package meant to be embedded, and it is worth knowing that it is gone.
