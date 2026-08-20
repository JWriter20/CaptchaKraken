/**
 * Regression: GeeTest's submit control is a DIV that says "OK", and the verify
 * finder could not see it.
 *
 * `getVerifyButton` looked for two shapes only — a `<button>` whose text
 * contains one of Verify / Next / Submit / Skip, and a `<div role="button">`
 * with the same texts — then two vendor fallbacks (`#recaptcha-verify-button`,
 * `.button-submit`). GeeTest matches none of them. Its control is
 *
 *     <div class="geetest_submit geetest_disable">OK</div>
 *
 * which is the wrong TAG (a bare div, no `role`) and the wrong TEXT ("OK" is
 * not on the list). So it returned null and nothing was ever pressed.
 *
 * WHAT THAT COST, AND WHY IT LOOKED LIKE THE MODEL
 *
 * On GeeTest's ordered icon-click the model was right and the driver threw the
 * answer away. Measured live on 2026-08-19: the model returned three points
 * that landed on the three reference icons in order, `executeClick` put the
 * cursor within 0.005 normalised of each requested centre — and then no OK.
 * The board does not grade until you press it, so the solve loop re-read the
 * same unchanged puzzle, re-answered it identically, and gave up after ten
 * rounds. It scored 0/31 and then 0/13, which reads exactly like a puzzle type
 * the model cannot do. Every one of those attempts was a correct answer.
 *
 * This is why the test asserts on the FINDER and not on a solve rate: a rate
 * cannot tell "answered wrongly" from "answered correctly and never sent".
 *
 * The Python driver has the identical list in `page_solver.py::_get_verify_button`
 * and was fixed in the same commit; `python/tests/test_geetest_submit_button.py`
 * pins that half. Per CLAUDE.md 1c the two ports must behave the same.
 *
 * A fake frame rather than a browser: what is under test is which element the
 * finder SELECTS out of a known DOM, and that is observable without Firefox.
 * The fake resolves the selector forms the finder actually uses, so it does not
 * presuppose which of them the fix reaches for.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

interface FakeEl {
  tag: string;
  classes: string[];
  id?: string;
  role?: string;
  text: string;
  visible?: boolean;
}

/** The GeeTest ordered-icon panel, as captured from gt4.geetest.com. */
const GEETEST_PANEL: FakeEl[] = [
  { tag: 'div', classes: ['geetest_box'], text: 'Select in this order OK' },
  // The control. Note `geetest_disable`: GeeTest greys it until enough icons
  // are picked, and it is a plain div throughout.
  { tag: 'div', classes: ['geetest_submit_14e1a298', 'geetest_submit', 'geetest_disable'], text: 'OK' },
  // Two decoys that also say "OK". A fix that matches on the word alone must
  // not settle on the tooltip.
  { tag: 'div', classes: ['geetest_submit_tips_14e1a298', 'geetest_submit_tips'], text: 'OK' },
];

/**
 * Enough of Playwright's `$` to answer the queries the finder makes: a CSS
 * class selector, a CSS id selector, and the xpath template it builds per
 * button text.
 */
function fakeFrame(dom: FakeEl[]) {
  const handle = (el: FakeEl) => ({
    __el: el,
    isVisible: async () => el.visible !== false,
  });

  return {
    async $(selector: string) {
      if (selector.startsWith('xpath=')) {
        // `.//button[contains(translate(., 'ABC…', 'abc…'), 'verify')] | .//div[…]`
        // Every quoted lowercase run is a candidate; the alphabet is
        // translate()'s own second argument, so drop it and keep the text.
        const alphabet = 'abcdefghijklmnopqrstuvwxyz';
        const wanted = [...selector.matchAll(/'([a-z]+)'/g)]
          .map((m) => m[1])
          .filter((s) => s !== alphabet);
        const needle = wanted[wanted.length - 1];
        const hit = dom.find(
          (el) =>
            (el.tag === 'button' || (el.tag === 'div' && el.role === 'button')) &&
            needle !== undefined &&
            el.text.toLowerCase().includes(needle),
        );
        return hit ? handle(hit) : null;
      }
      if (selector.startsWith('#')) {
        const hit = dom.find((el) => el.id === selector.slice(1));
        return hit ? handle(hit) : null;
      }
      // Class selector, possibly several in one string.
      const classes = selector.split('.').filter(Boolean);
      const hit = dom.find((el) => classes.every((c) => el.classes.includes(c)));
      return hit ? handle(hit) : null;
    },
  };
}

function finder() {
  return new CaptchaKrakenSolver({}) as any;
}

test('the GeeTest panel really does defeat the old two shapes', () => {
  // Guards the premise. If GeeTest ever ships a <button>Verify</button> this
  // whole regression is moot, and the test should say so rather than pass by
  // asserting something that stopped being true.
  const submit = GEETEST_PANEL.find((el) => el.classes.includes('geetest_submit'))!;
  assert.equal(submit.tag, 'div', 'GeeTest submit is a bare div');
  assert.equal(submit.role, undefined, 'and carries no role="button"');
  assert.ok(
    !['verify', 'next', 'submit', 'skip'].some((t) => submit.text.toLowerCase().includes(t)),
    'and its text is none of Verify/Next/Submit/Skip',
  );
});

test('getVerifyButton finds the GeeTest OK control', async () => {
  const solver = finder();
  const found = await solver.getVerifyButton(fakeFrame(GEETEST_PANEL));

  assert.ok(
    found,
    'GeeTest\'s submit was not found, so a correctly answered icon puzzle is '
    + 'never sent — the board is re-read and re-answered until the round cap.',
  );
  assert.ok(
    found.__el.classes.includes('geetest_submit'),
    `found the wrong element (${JSON.stringify(found.__el.classes)}); `
    + 'geetest_submit_tips is a tooltip, not the control',
  );
});

test('the vendor fallbacks still win where they should', async () => {
  const solver = finder();

  const recaptcha = await solver.getVerifyButton(
    fakeFrame([{ tag: 'button', classes: [], id: 'recaptcha-verify-button', text: '' }]),
  );
  assert.equal(recaptcha?.__el.id, 'recaptcha-verify-button');

  const hcaptcha = await solver.getVerifyButton(
    fakeFrame([{ tag: 'div', classes: ['button-submit'], text: '' }]),
  );
  assert.ok(hcaptcha?.__el.classes.includes('button-submit'));
});

test('a real Verify button is still preferred over anything else', async () => {
  const solver = finder();
  const found = await solver.getVerifyButton(
    fakeFrame([
      { tag: 'button', classes: ['real-verify'], text: 'Verify' },
      { tag: 'div', classes: ['geetest_submit'], text: 'OK' },
    ]),
  );
  assert.ok(
    found?.__el.classes.includes('real-verify'),
    'the named-text pass must keep first refusal; GeeTest is a fallback',
  );
});

test('an invisible control is not offered', async () => {
  const solver = finder();
  const found = await solver.getVerifyButton(
    fakeFrame([{ tag: 'div', classes: ['geetest_submit'], text: 'OK', visible: false }]),
  );
  assert.equal(found, null, 'a hidden submit must not be returned as pressable');
});
