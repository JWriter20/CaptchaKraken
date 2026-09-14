import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import { vendorFromSrc } from './solver';

const REAL_CHALLENGE =
  'https://newassets.hcaptcha.com/captcha/v1/3f1a2b/static/hcaptcha.html#frame=challenge&id=0x1&host=example.com';
const REAL_CHECKBOX =
  'https://newassets.hcaptcha.com/captcha/v1/3f1a2b/static/hcaptcha.html#frame=checkbox&id=0x1&host=example.com';
const FIXTURE_CHALLENGE =
  'http://127.0.0.1:8080/frame?hcaptcha.html#frame=challenge';

test('the vendor challenge is hCaptcha', () => {
  assert.equal(vendorFromSrc(REAL_CHALLENGE), 'hcaptcha');
  assert.equal(vendorFromSrc(REAL_CHECKBOX), 'hcaptcha');
});

test('an hCaptcha frame not served off the apex host is still hCaptcha', () => {
  assert.equal(vendorFromSrc(FIXTURE_CHALLENGE), 'hcaptcha');
});

test('the hint matches the same substring the DOM selectors do', () => {
  const source = readFileSync(resolve(__dirname, '..', 'src', 'solver.ts'), 'utf8');
  assert.ok(
    source.includes('iframe[src*="hcaptcha"][src*="frame=challenge"]'),
    'the challenge selector moved — re-check what vendorFromSrc keys on',
  );
  assert.ok(
    !/includes\('hcaptcha\.com'\)/.test(source),
    'the apex host is being matched again somewhere; the shape gate will not engage',
  );
});

test('reCAPTCHA is named by its api2 path', () => {
  assert.equal(vendorFromSrc('https://www.google.com/recaptcha/api2/bframe?k=6Le'), 'recaptcha');
  assert.equal(vendorFromSrc('https://www.google.com/recaptcha/api2/anchor?k=6Le'), 'recaptcha');

  assert.equal(vendorFromSrc('https://recaptcha.net/recaptcha/api2/bframe?k=6Le'), 'recaptcha');
});

test('"recaptcha" does not read as "hcaptcha"', () => {
  assert.equal(vendorFromSrc('/recaptcha/api2/bframe'), 'recaptcha');
});

test('anything else is unknown, and unknown is allowed every grid shape', () => {
  for (const src of ['https://api.geetest.com/gt.js', '.yidun_panel', '', null, undefined]) {
    assert.equal(vendorFromSrc(src), 'unknown');
  }
});
