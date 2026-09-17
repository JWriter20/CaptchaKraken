// A child process the driver never bounds is a solve with no ceiling.
//
// The inference CLI runs as a child process and the solve deadline is only read between steps, so an ask
// already in flight is bounded by nothing the loop does. `execFile` has no default timeout at all, and the
// Python planner behind it used to send every request with a hardcoded 120s — 2.7x the whole 45s budget.
// Measured against the hosted endpoint on 2026-09-17: one ask hung and gave up after ~124s, and the attempt
// ran 143.3s.
//
// The source test is the one that keeps this true: a new call site is one line, and the floor and ceiling
// only matter if every ask goes through them.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import { CaptchaKrakenSolver } from './solver';

const SOLVER = readFileSync(path.join(__dirname, '..', 'src', 'solver.ts'), 'utf8');

test('every ask the solver spends its budget on is bounded', () => {
  const asks = [...SOLVER.matchAll(/execFileAsync\(py, args,[^;]*/g)].map((m) => m[0]);
  assert.ok(asks.length >= 2, `expected the still and keyframe asks, found ${asks.length}`);
  for (const ask of asks) {
    assert.match(ask, /timeout: this\.askTimeoutMs\(\)/,
      'an ask runs the model with no bound on how long it may take');
    assert.match(ask, /killSignal: 'SIGKILL'/,
      'a hung ask must be killed, not asked politely while the budget burns');
  }
});

test('the bound is what is left of the solve', () => {
  const solver: any = new CaptchaKrakenSolver({});
  solver.solveDeadlineAt = Date.now() + 20_000;
  const left = solver.askTimeoutMs();
  assert.ok(left > 18_000 && left <= 20_000, `asked for ${left}ms of a 20s remainder`);
});

test('a nearly spent budget still buys a real attempt', () => {
  const solver: any = new CaptchaKrakenSolver({});
  solver.solveDeadlineAt = Date.now() + 500;
  assert.equal(solver.askTimeoutMs(), 10_000);
});

test('a long budget does not raise the ceiling', () => {
  const solver: any = new CaptchaKrakenSolver({});
  solver.solveDeadlineAt = Date.now() + 600_000;
  assert.equal(solver.askTimeoutMs(), 120_000);
});

test('a caller with no deadline set gets the ceiling', () => {
  const solver: any = new CaptchaKrakenSolver({});
  solver.solveDeadlineAt = 0;
  assert.equal(solver.askTimeoutMs(), 120_000);
});
