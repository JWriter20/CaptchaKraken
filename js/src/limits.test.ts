/**
 * The ordering 5 < 8 < 10 is a real constraint, so it gets a real test.
 *
 * It had been described as "pinned by a test" when nothing pinned it: this
 * package's `npm test` was `tsc --noEmit`, which cannot notice a changed
 * integer. A number three separate comments call load-bearing, that any edit
 * could silently break, was resting on someone remembering.
 *
 * `node --test` rather than a framework: this is the only JS test in the
 * package and it needs no runner, no config, and no dependency to run in CI.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS,
  SERVER_MAX_BILLABLE_ROUNDS,
  SERVER_MAX_SERVED_ROUNDS,
} from './limits';

test('the client gives up after the free rounds but before the server refuses', () => {
  // The whole invariant in one line. If this fails, read limits.ts before
  // changing the number to make it pass.
  assert.ok(
    SERVER_MAX_BILLABLE_ROUNDS < DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS &&
      DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS < SERVER_MAX_SERVED_ROUNDS,
    `Expected ${SERVER_MAX_BILLABLE_ROUNDS} < ${DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS} < ` +
      `${SERVER_MAX_SERVED_ROUNDS}. The client must try rounds the customer is not ` +
      'charged for, and must stop before the gateway answers with a 409.',
  );
});

test('there is room on BOTH sides, not just ordering', () => {
  // Strict inequality alone would be satisfied by 6 or 9, either of which
  // leaves one of the two margins a single round wide — enough to satisfy the
  // assertion above while defeating the reason for it.
  assert.ok(
    DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS - SERVER_MAX_BILLABLE_ROUNDS >= 2,
    'Too few free rounds above the billing cap to be worth having.',
  );
  assert.ok(
    SERVER_MAX_SERVED_ROUNDS - DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS >= 2,
    'Too little headroom below the server refusal; a hard puzzle would routinely 409.',
  );
});

test('the server-side mirrors still match the gateway', () => {
  // These duplicate captchakraken-gateway/src/pricing.ts. A cross-repo import
  // is not available here, so the duplication is explicit and asserted rather
  // than implicit and forgotten. If the gateway moves either number, this test
  // is the reminder that this file has to move too.
  assert.equal(SERVER_MAX_BILLABLE_ROUNDS, 5, 'gateway MAX_BILLABLE_ROUNDS_PER_SESSION');
  assert.equal(SERVER_MAX_SERVED_ROUNDS, 10, 'gateway MAX_ROUNDS_PER_SESSION');
});

test('the documented default is the one the solver actually uses', () => {
  assert.equal(DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS, 8);
});
