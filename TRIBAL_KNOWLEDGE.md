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

**Nothing may be written to stdout by the MCP server.** On the stdio transport
stdout *is* the protocol channel, so one stray `console.log` — a banner, a
deprecation notice — corrupts the stream and the client reports the server as
broken rather than as chatty. Diagnostics go to stderr, which clients surface in
their logs.

**A minted API key is written straight to a `0600` file and never returned
through the conversation.** The obvious move for an agent is to echo the key it
just created, and a transcript is not a secret store. `create_api_key` reports
only the path, and the solver reads that file with no environment variable set.

## Known gaps

**There is no generated code map in this repo.** Nothing produces
`repomix-output.md` here, so the rule about grepping a map before writing a new
helper is served by `FILE_PURPOSES.md` plus grep over the tree. Recorded as a gap
rather than closed by adding a dependency to a package that has few.

**Nothing in CI checks `FILE_PURPOSES.md` against `git ls-files`.** Both
directions — a tracked file with no entry, and an entry for a file that is gone —
have to be checked by hand today, which means the map can go stale between
commits.

**Two test files target the retired v1 architecture and are visibly skipped.**
They import a module that is not in this repo, so they can never run; an explicit
`importorskip` puts the skip in the run output instead of leaving the files
silently uncollected. Either port them to the current planner or delete them — a
test that can never run is not coverage.

**The grid-detection corpus of real captures is not in this repo.** The tests
that measure detection rates over it skip when the directory is absent, which is
the case in every clean checkout, so the hermetic gate covers the geometry rules
rather than the rates.
