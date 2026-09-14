import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS,
  SERVER_MAX_BILLABLE_ROUNDS,
  SERVER_MAX_SERVED_ROUNDS,
} from './limits';

test('the client gives up after the free rounds but before the server refuses', () => {
  assert.ok(
    SERVER_MAX_BILLABLE_ROUNDS < DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS &&
      DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS < SERVER_MAX_SERVED_ROUNDS,
    `Expected ${SERVER_MAX_BILLABLE_ROUNDS} < ${DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS} < ` +
      `${SERVER_MAX_SERVED_ROUNDS}. The client must try rounds the customer is not ` +
      'charged for, and must stop before the gateway answers with a 409.',
  );
});

test('there is room on BOTH sides, not just ordering', () => {
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
  assert.equal(SERVER_MAX_BILLABLE_ROUNDS, 5, 'gateway MAX_BILLABLE_ROUNDS_PER_SESSION');
  assert.equal(SERVER_MAX_SERVED_ROUNDS, 10, 'gateway MAX_ROUNDS_PER_SESSION');
});

test('the documented default is the one the solver actually uses', () => {
  assert.equal(DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS, 8);
});
