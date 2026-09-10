/**
 * `PhaseBudget` is how "the JS port is four seconds slower" becomes a bug report.
 *
 * It is always on and always returned on `SolveResult.phases`, so its numbers
 * are read by anyone diagnosing a slow solve — and by the driver gate comparing
 * the two ports phase by phase. Two of its rules are deliberate and easy to
 * "fix" into something wrong:
 *
 *   - Re-entering a phase of the SAME name does not accumulate again. A burst
 *     that contains screenshots that are themselves timed must not report its
 *     own span twice.
 *   - A phase nested inside a DIFFERENTLY named one counts under both. The
 *     cursor drifting over the widget while the model generates really is both
 *     `mouse` and `inference`. The totals are an attribution, not a partition,
 *     and may exceed the elapsed time.
 *
 * The assertions below avoid wall-clock thresholds on purpose: a timing test
 * that races the scheduler is a flake, and a flake in a gate is worse than the
 * gap it covers. What is pinned is attribution and counting, which are exact.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PhaseBudget, PRODUCTIVE, timingsEnabled } from './timing';

test('a phase records its span once and returns the value', async () => {
  const budget = new PhaseBudget();

  const value = await budget.phase('inference', async () => 'answer');

  assert.equal(value, 'answer');
  assert.equal(budget.counts.get('inference'), 1);
  assert.ok((budget.totals.get('inference') ?? -1) >= 0);
});

test('re-entering the same phase does not count it twice', async () => {
  // A burst is timed, and so is each screenshot inside it. Counting the inner
  // entry would report a phase that took longer than the solve.
  const budget = new PhaseBudget();

  await budget.phase('burst', async () => {
    await budget.phase('burst', async () => 'inner');
    await budget.phase('burst', async () => 'inner again');
  });

  assert.equal(budget.counts.get('burst'), 1, 'a nested re-entry was accumulated');
});

test('a differently named phase inside another counts under both', async () => {
  const budget = new PhaseBudget();

  await budget.phase('inference', async () => {
    await budget.phase('mouse', async () => 'drifting');
  });

  assert.equal(budget.counts.get('inference'), 1);
  assert.equal(budget.counts.get('mouse'), 1);
});

test('a phase that throws is still recorded, and the error still escapes', async () => {
  // The slow phase is very often the failing one. Losing its time because it
  // threw is losing the measurement that explains the failure.
  const budget = new PhaseBudget();

  await assert.rejects(
    budget.phase('screenshot', async () => {
      throw new Error('element detached');
    }),
    /element detached/,
  );

  assert.equal(budget.counts.get('screenshot'), 1);
});

test('a phase that throws does not leave the name marked as open', async () => {
  // If the name were left on the open stack, every later entry would be treated
  // as a re-entry and silently stop being counted for the rest of the solve.
  const budget = new PhaseBudget();

  await assert.rejects(budget.phase('grid', async () => {
    throw new Error('boom');
  }));
  await budget.phase('grid', async () => 'fine');

  assert.equal(budget.counts.get('grid'), 2, 'the phase stopped counting after it threw once');
});

test('directly added spans accumulate and count', () => {
  const budget = new PhaseBudget();

  budget.add('wait', 120);
  budget.add('wait', 80);

  assert.equal(budget.totals.get('wait'), 200);
  assert.equal(budget.counts.get('wait'), 2);
});

test('the reported object carries every phase plus a total', () => {
  // This object is `SolveResult.phases`. A missing `total` makes every consumer
  // recompute it from the parts, which is wrong here because the parts overlap.
  const budget = new PhaseBudget();
  budget.add('inference', 900);
  budget.add('mouse', 300);

  const out = budget.toObject();

  assert.equal(out.inference, 900);
  assert.equal(out.mouse, 300);
  assert.ok(Number.isFinite(out.total));
});

test('the report separates useful time from waiting, and marks which is which', () => {
  const budget = new PhaseBudget();
  budget.add('inference', 2000);
  budget.add('settle', 5000);

  const report = budget.report();

  assert.match(report, /useful/);
  assert.match(report, /waiting/);
  assert.match(report, /\* inference/, 'a productive phase is not marked as one');
  assert.match(report, /\s{2}settle/, 'a waiting phase should not carry the productive marker');
  assert.ok(PRODUCTIVE.has('inference') && !PRODUCTIVE.has('settle'));
});

test('a budget with no phases still reports rather than dividing by zero', () => {
  // Reached on a solve that failed before its first phase.
  const report = new PhaseBudget().report();
  assert.match(report, /\[BUDGET\]/);
  assert.doesNotMatch(report, /NaN/);
});

test('printing is opt-in and reads exactly "1"', () => {
  // Anything else being treated as on would put budget lines into the output of
  // every consumer who set CAPTCHA_TIMINGS=0 to turn them off.
  const before = process.env.CAPTCHA_TIMINGS;
  try {
    process.env.CAPTCHA_TIMINGS = '1';
    assert.equal(timingsEnabled(), true);
    process.env.CAPTCHA_TIMINGS = '0';
    assert.equal(timingsEnabled(), false);
    process.env.CAPTCHA_TIMINGS = 'true';
    assert.equal(timingsEnabled(), false);
    delete process.env.CAPTCHA_TIMINGS;
    assert.equal(timingsEnabled(), false);
  } finally {
    if (before === undefined) delete process.env.CAPTCHA_TIMINGS;
    else process.env.CAPTCHA_TIMINGS = before;
  }
});
