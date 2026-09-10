/**
 * The hosted-API refusal has to survive the process boundary intact.
 *
 * This port does not talk to the gateway. It shells out to the Python CLI,
 * which writes the refusal to stderr as `{error, ck_error:{...}}` — snake_case,
 * because that is Python's half — and `parseApiError` turns it back into the
 * one error class this package exports. The documented rule for callers is
 * "branch on `e.code`, never on the message", and that rule is only true if
 * every field crosses.
 *
 * The failure it prevents is specific and silent: a field name that does not
 * survive the translation arrives as `undefined`, so `e.code ===
 * 'insufficient_credits'` never matches, and a customer who is out of credits
 * gets an infinite retry loop instead of a top-up link.
 *
 * The payload below is the exact shape `CaptchaKrakenAPIError.to_payload()`
 * produces on the Python side — the same object its own test pins — so the two
 * halves are checked against one contract rather than two guesses.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenAPIError, parseApiError } from './errors';

/** What the Python client writes to stderr, verbatim. */
const PYTHON_PAYLOAD = JSON.stringify({
  error: 'CaptchaKraken is out of credits. Top up at https://captchakraken.com/dashboard',
  ck_error: {
    status: 402,
    code: 'insufficient_credits',
    resolution_url: 'https://captchakraken.com/dashboard',
    retry_after_seconds: null,
  },
});

test('every field of a refusal survives the boundary, renamed but not lost', () => {
  const err = parseApiError(PYTHON_PAYLOAD);

  assert.ok(err instanceof CaptchaKrakenAPIError);
  assert.equal(err.code, 'insufficient_credits');
  assert.equal(err.status, 402);
  assert.equal(err.resolutionUrl, 'https://captchakraken.com/dashboard');
  assert.match(err.message, /top up/i);
});

test('the refusal is found even when stderr carries other lines around it', () => {
  // Real stderr: timing records, warnings, then the payload. `JSON.parse` of the
  // whole buffer fails on all of it, which is why the scan is line by line.
  const stderr = [
    '[timing] phase=detect ms=412',
    'warning: falling back to python3',
    PYTHON_PAYLOAD,
    '[timing] phase=total ms=980',
  ].join('\n');

  const err = parseApiError(stderr);
  assert.equal(err?.code, 'insufficient_credits');
});

test('retry_after_seconds arrives as a number a caller can wait on', () => {
  const err = parseApiError(
    JSON.stringify({
      error: 'Rate limited.',
      ck_error: { status: 429, code: 'rate_limited', retry_after_seconds: 7.5 },
    }),
  );

  assert.equal(err?.retryAfterSeconds, 7.5);
  assert.equal(typeof err?.retryAfterSeconds, 'number');
});

test('stderr with nothing of ours in it yields null, not a confident guess', () => {
  // The caller's generic handling has to stay in place. Inventing a billing
  // error from an unrelated crash would send someone to a top-up page over a
  // missing browser binary.
  assert.equal(parseApiError(''), null);
  assert.equal(parseApiError('Traceback (most recent call last): ...'), null);
  assert.equal(parseApiError('{"error":"something else"}'), null);
});

test('a corrupt line does not stop the scan reaching a good one', () => {
  // A partial write, or a line that merely mentions ck_error in prose.
  const stderr = ['{"ck_error": truncated…', 'note: ck_error follows', PYTHON_PAYLOAD].join('\n');

  assert.equal(parseApiError(stderr)?.code, 'insufficient_credits');
});

test('a field of the wrong type is dropped rather than passed through', () => {
  // The gateway is a separate service. A schema drift that turns status into a
  // string must not produce `e.status === "402"`, which no caller compares
  // against and every caller would get wrong.
  const err = parseApiError(
    JSON.stringify({
      error: 'Refused.',
      ck_error: {
        status: '402',
        code: 402,
        resolution_url: 12345,
        retry_after_seconds: 'soon',
      },
    }),
  );

  assert.ok(err instanceof CaptchaKrakenAPIError);
  assert.equal(err.status, undefined);
  assert.equal(err.code, undefined);
  assert.equal(err.resolutionUrl, undefined);
  assert.equal(err.retryAfterSeconds, undefined);
});

test('an empty message becomes a sentence, never an empty error', () => {
  const err = parseApiError(
    JSON.stringify({ error: '   ', ck_error: { status: 403, code: 'account_suspended' } }),
  );

  assert.notEqual(err?.message.trim(), '');
  assert.equal(err?.code, 'account_suspended');
});

test('an unknown code arrives intact instead of being coerced', () => {
  // The gateway ships independently of this package. A code added there after
  // this release must reach the caller as itself, so they can at least log it.
  const err = parseApiError(
    JSON.stringify({ error: 'Nope.', ck_error: { status: 400, code: 'a_brand_new_code' } }),
  );

  assert.equal(err?.code, 'a_brand_new_code');
});

test('the error is branchable without instanceof', () => {
  // Two copies of the package in one dependency tree make `instanceof` fail,
  // and a caught error that cannot be identified is handled as a generic crash.
  const err = parseApiError(PYTHON_PAYLOAD);
  assert.equal((err as CaptchaKrakenAPIError).isCaptchaKrakenAPIError, true);
  assert.equal(err?.name, 'CaptchaKrakenAPIError');
});

test('it is a real Error, so existing catch-and-rethrow paths keep working', () => {
  const err = parseApiError(PYTHON_PAYLOAD)!;
  assert.ok(err instanceof Error);
  assert.ok(typeof err.stack === 'string' && err.stack.length > 0);
});
