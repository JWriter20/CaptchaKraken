/**
 * The vendor hint is what stops a drag puzzle being answered as a grid.
 *
 * `vendorFromSrc` looks like a cosmetic label and is not. It feeds the grid
 * SHAPE GATE, which restricts hCaptcha to a 3x3 and reCAPTCHA to a 3x3 or 4x4,
 * and restricts everything else not at all — GeeTest and Prosopo ship real 3x3
 * grids, so `unknown` has to stay permissive. A vendor the client cannot name
 * therefore loses the only check that stops `find_grid` reading the header and
 * footer bands of a click board as a lattice.
 *
 * It was keyed on `hcaptcha.com` while the seven `iframe[src*="hcaptcha"]`
 * selectors elsewhere in solver.ts were keyed on `hcaptcha`. Measured on Tier
 * 3: two hCaptcha fixtures routed to the grid expert where the registry says
 * pixel, and this port took 11 actions across 7 boards whose answer is one drag
 * each — a grid answer is a LIST OF CELLS, so a mis-route does not read as a
 * wrong answer, it reads as a driver clicking everything.
 *
 * The strings below are real: the vendor's own challenge URL, and the fixture
 * marker our own fixture chrome copies from it.
 */
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
  // The regression. Reported `unknown`, which switches the shape gate off.
  assert.equal(vendorFromSrc(FIXTURE_CHALLENGE), 'hcaptcha');
});

test('the hint matches the same substring the DOM selectors do', () => {
  // Seven `iframe[src*="hcaptcha"][src*="frame=..."]` selectors find the
  // widget; this decides how it is answered. They must agree on what hCaptcha
  // is, or the client solves a board it found by a rule it then contradicts.
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
  // recaptcha.net serves the same api2 paths.
  assert.equal(vendorFromSrc('https://recaptcha.net/recaptcha/api2/bframe?k=6Le'), 'recaptcha');
});

test('"recaptcha" does not read as "hcaptcha"', () => {
  // The two checks are ordered hCaptcha first; this pins that the ordering is
  // harmless rather than lucky.
  assert.equal(vendorFromSrc('/recaptcha/api2/bframe'), 'recaptcha');
});

test('anything else is unknown, and unknown is allowed every grid shape', () => {
  for (const src of ['https://api.geetest.com/gt.js', '.yidun_panel', '', null, undefined]) {
    assert.equal(vendorFromSrc(src), 'unknown');
  }
});
