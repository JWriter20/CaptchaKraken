/**
 * Regression: every element screenshot must name its own timeout.
 *
 * Playwright's `screenshot()` defaults to a 30-SECOND timeout and waits for the
 * element to be visible and stable first. That is fine for an element that is
 * there, and catastrophic for one that is going away — which is the normal
 * state of a challenge iframe right after a submit, while the vendor tears it
 * down. One such call hangs for the full 30s.
 *
 * This has now bitten twice. `waitForElementSettled` carries the scar tissue in
 * a comment: "a closing/animating challenge element otherwise makes Playwright's
 * default 30s stability wait hang per screenshot (that's what made a multi-round
 * solve take ~115s)". `waitForGridCellsLoaded` was written with the same poll
 * shape and without the timeout, so after the reCAPTCHA grid driver clicked
 * Verify, the next loop spent 30s photographing a dead iframe before concluding
 * the puzzle was solved. Measured live: submit at 24.5s, verdict at 64.7s — a
 * 38.5s tail on a solve whose actual work took twelve seconds, paid by every
 * customer on every multi-round reCAPTCHA.
 *
 * Worse, a poll loop makes the default look harmless. Its own budget
 * (`while (Date.now() - start < timeout)`) is only consulted BETWEEN
 * iterations, so a single hung screenshot sails straight past an 8s cap.
 *
 * So this is a structural test rather than a behavioural one: it reads the
 * source and insists that every screenshot taken on an element handle passes a
 * `timeout`. A behavioural test would need a real browser tearing down a real
 * iframe, and would still only cover the call sites someone thought to exercise.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

/**
 * The SOURCE, not the build. `npm test` compiles into .test-build/ and runs
 * from there, where __dirname holds solver.js — so resolving beside the test
 * finds a file whose comments and option objects have already been through tsc.
 * The package root is the stable anchor; the __dirname fallback keeps this
 * runnable straight from src/ under a ts runner.
 */
const CANDIDATES = [
  path.join(process.cwd(), 'src', 'solver.ts'),
  path.join(__dirname, 'solver.ts'),
  path.join(__dirname, '..', 'src', 'solver.ts'),
];
const SOLVER_PATH = CANDIDATES.find(existsSync);
if (!SOLVER_PATH) throw new Error(`cannot find solver.ts; looked in ${CANDIDATES.join(', ')}`);
const SOLVER = readFileSync(SOLVER_PATH, 'utf8');

/**
 * Every `<something>.screenshot({ ... })` call, with the receiver and options.
 *
 * Deliberately matches the OPTIONS-OBJECT form only. `el.screenshot()` with no
 * arguments returns a buffer and is used for cheap byte comparisons; those are
 * flagged separately below, since they carry the same default.
 */
const CALLS = [...SOLVER.matchAll(/(\w+)\.screenshot\(\{([^}]*)\}/g)];

test('the solver takes screenshots somewhere', () => {
  // If a refactor renames the call or changes its shape, this file would pass
  // by matching nothing at all — which is the failure mode of every test that
  // greps for something.
  assert.ok(CALLS.length >= 5, `expected several screenshot calls, found ${CALLS.length}`);
});

test('every element screenshot passes an explicit timeout', () => {
  const offenders = CALLS
    // `page.screenshot` is a viewport grab: there is no element to wait on
    // becoming stable, so the 30s default cannot hang the same way.
    .filter(([, receiver]) => receiver !== 'page')
    .filter(([, , options]) => !/\btimeout\s*:/.test(options))
    .map(([full]) => full.replace(/\s+/g, ' ').slice(0, 90));

  assert.deepEqual(
    offenders,
    [],
    'these element screenshots would inherit Playwright\'s 30s default and hang '
    + 'on a challenge that is being torn down:\n  ' + offenders.join('\n  '),
  );
});

test('the bare-form element screenshots are the known cheap ones', () => {
  // `el.screenshot()` with no options also inherits the 30s default. There are
  // a couple on purpose — byte-comparison probes where a throw is caught and
  // treated as "cannot film it" — so this pins the COUNT rather than banning
  // the form, and a new one has to be looked at.
  const bare = [...SOLVER.matchAll(/(\w+)\.screenshot\(\)/g)].filter(([, r]) => r !== 'page');

  assert.ok(
    bare.length <= 2,
    `${bare.length} bare element screenshots; each inherits the 30s default. `
    + 'If a new one is deliberate, raise this bound and say why.',
  );
});
