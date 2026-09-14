// GeeTest's control is `<div class="geetest_submit geetest_disable">OK</div>` (wrong tag, wrong word) beside decoy tooltips that also
// say OK; it scored 0/31 then 0/13, which reads exactly like a puzzle the model cannot do. Pins the finder, not a rate.
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

const GEETEST_PANEL: FakeEl[] = [
  { tag: 'div', classes: ['geetest_box'], text: 'Select in this order OK' },

  { tag: 'div', classes: ['geetest_submit_14e1a298', 'geetest_submit', 'geetest_disable'], text: 'OK' },

  { tag: 'div', classes: ['geetest_submit_tips_14e1a298', 'geetest_submit_tips'], text: 'OK' },
];

function fakeFrame(dom: FakeEl[]) {
  const handle = (el: FakeEl) => ({
    __el: el,
    isVisible: async () => el.visible !== false,
  });

  return {
    async $(selector: string) {
      if (selector.startsWith('xpath=')) {
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
