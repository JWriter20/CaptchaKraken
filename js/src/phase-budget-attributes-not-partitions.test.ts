// Phases attribute rather than partition, a throwing phase is still recorded, and printing is on only under CAPTCHA_TIMINGS=1.
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
  const budget = new PhaseBudget();

  await assert.rejects(budget.phase('grid', async () => {
    throw new Error('boom');
  }));
  await budget.phase('grid', async () => 'fine');

  assert.equal(budget.counts.get('grid'), 2, 'the phase stopped counting after it threw once');
});

test('directly added spans accumulate and count', () => {
  const budget = new PhaseBudget();

  budget.add('detect', 120);
  budget.add('detect', 80);

  assert.equal(budget.totals.get('detect'), 200);
  assert.equal(budget.counts.get('detect'), 2);
});

test('the reported object carries every phase plus a total', () => {
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
  const report = new PhaseBudget().report();
  assert.match(report, /\[BUDGET\]/);
  assert.doesNotMatch(report, /NaN/);
});

test('printing is opt-in and reads exactly "1"', () => {
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
