// GeeTest paints success inside the open panel with no token, and `geetest_popup_wrap` carries the same success class at zero
// height while the panel is shut. Before visibility was part of the test it cost 34 model calls for 20 drags.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';
import { SELECTORS } from './selectors';
import { Vendor } from './kinds';
import { fakeDom } from './fake-dom.test';

const ACCEPTED = (SELECTORS[Vendor.GEETEST].accepted as string).split(',').map((s) => s.trim());

/** Which of the accepted selectors an element with these classes answers to. */
const byClass = (cls: string, visible: boolean) => ({
  matches: ACCEPTED.filter((sel) => sel.split('.').filter(Boolean).every((c) => cls.split(/\s+/).includes(c))),
  visible,
});

const solved = (nodes: Array<{ cls: string; visible: boolean }>) =>
  (new CaptchaKrakenSolver({}) as any).isCaptchaSolved(fakeDom(nodes.map((n) => byClass(n.cls, n.visible))));

test('the accept banner inside the open panel is a solve', async () => {
  assert.equal(await solved([{ cls: 'geetest_result_tips geetest_success geetest_showResult', visible: true }]), true);
});

test('the locked anchor after the panel closes is a solve', async () => {
  assert.equal(await solved([{ cls: 'geetest_captcha geetest_customTheme geetest_lock_success', visible: true }]), true);
});

test('a REFUSED drag is not a solve', async () => {
  assert.equal(await solved([
    { cls: 'geetest_result_tips geetest_fail geetest_showResult', visible: true },
    { cls: 'geetest_captcha geetest_customTheme geetest_freeze_wait geetest_fail', visible: true },
  ]), false);
});

test('an untouched widget is not a solve', async () => {
  assert.equal(await solved([
    { cls: 'geetest_result_tips', visible: true },
    { cls: 'geetest_captcha geetest_customTheme', visible: true },
  ]), false);
});

test('the closed popup wrapper does not count, class or no class', async () => {
  assert.equal(await solved([{ cls: 'geetest_popup_wrap geetest_popup geetest_customTheme geetest_lock_success', visible: false }]), false);
});

test('a page with no GeeTest on it is not a solve', async () => {
  assert.equal(await solved([]), false);
});
