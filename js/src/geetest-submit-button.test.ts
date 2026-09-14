// GeeTest's control is `<div class="geetest_submit geetest_disable">OK</div>` (wrong tag, wrong word) beside decoy tooltips that also
// say OK; it scored 0/31 then 0/13, which reads exactly like a puzzle the model cannot do. Pins the finder, not a rate.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';
import { SUBMIT_SELECTORS } from './selectors';
import { fakeDom, FakeNode } from './fake-dom.test';

interface FakeEl {
  tag: string;
  classes: string[];
  id?: string;
  role?: string;
  text: string;
  visible?: boolean;
}

/** Which submit selectors an element answers to, read the way a CSS/xpath engine would. */
function node(el: FakeEl): FakeNode & { el: FakeEl } {
  const matches = SUBMIT_SELECTORS.filter((sel) => {
    if (sel.startsWith('xpath=')) {
      const needle = [...sel.matchAll(/'([a-z]+)'\)/g)].map((m) => m[1]).pop() as string;
      return (el.tag === 'button' || (el.tag === 'div' && el.role === 'button')) && el.text.toLowerCase().includes(needle);
    }
    return sel.startsWith('#') ? el.id === sel.slice(1) : sel.split('.').filter(Boolean).every((c) => el.classes.includes(c));
  });
  return { el, matches, visible: el.visible, text: el.text };
}

const GEETEST_PANEL: FakeEl[] = [
  { tag: 'div', classes: ['geetest_box'], text: 'Select in this order OK' },
  { tag: 'div', classes: ['geetest_submit_14e1a298', 'geetest_submit', 'geetest_disable'], text: 'OK' },
  { tag: 'div', classes: ['geetest_submit_tips_14e1a298', 'geetest_submit_tips'], text: 'OK' },
];

const find = async (dom: FakeEl[]): Promise<FakeEl | null> => {
  const found = await (new CaptchaKrakenSolver({}) as any).getVerifyButton(fakeDom(dom.map(node)));
  return found ? found.node.el : null;
};

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
  const found = await find(GEETEST_PANEL);
  assert.ok(found, 'GeeTest\'s submit was not found, so a correctly answered icon puzzle is never sent — the board is re-read and re-answered until the round cap.');
  assert.ok(found.classes.includes('geetest_submit'), `found the wrong element (${JSON.stringify(found.classes)}); geetest_submit_tips is a tooltip, not the control`);
});

test('the vendor fallbacks still win where they should', async () => {
  const recaptcha = await find([{ tag: 'button', classes: [], id: 'recaptcha-verify-button', text: '' }]);
  assert.equal(recaptcha?.id, 'recaptcha-verify-button');

  const hcaptcha = await find([{ tag: 'div', classes: ['button-submit'], text: '' }]);
  assert.ok(hcaptcha?.classes.includes('button-submit'));
});

test('a real Verify button is still preferred over anything else', async () => {
  const found = await find([
    { tag: 'div', classes: ['geetest_submit'], text: 'OK' },
    { tag: 'button', classes: ['real-verify'], text: 'Verify' },
  ]);
  assert.ok(found?.classes.includes('real-verify'), 'the named-text pass must keep first refusal; GeeTest is a fallback');
});

test('an invisible control is not offered', async () => {
  assert.equal(await find([{ tag: 'div', classes: ['geetest_submit'], text: 'OK', visible: false }]), null, 'a hidden submit must not be returned as pressable');
});
