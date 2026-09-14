// GeeTest paints success inside the open panel with no token, and `geetest_popup_wrap` carries the same success class at zero
// height while the panel is shut. Before visibility was part of the test it cost 34 model calls for 20 drags.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

function pageShowing(nodes: Array<{ cls: string; visible: boolean }>) {
  const matches = (sel: string, cls: string) =>
    sel.split(',').map((s) => s.trim()).some((one) =>
      one.split('.').filter(Boolean).every((c) => cls.split(/\s+/).includes(c)));
  return {
    async $$(sel: string) {
      return nodes.filter((n) => matches(sel, n.cls))
        .map((n) => ({ isVisible: async () => n.visible }));
    },
    async $() { return null; },
  } as any;
}

const solver = (): any => new CaptchaKrakenSolver({});

test('the accept banner inside the open panel is a solve', async () => {
  const page = pageShowing([
    { cls: 'geetest_result_tips geetest_success geetest_showResult', visible: true },
  ]);
  assert.equal(await solver().isGeetestAccepted(page), true);
});

test('the locked anchor after the panel closes is a solve', async () => {
  const page = pageShowing([
    { cls: 'geetest_captcha geetest_customTheme geetest_lock_success', visible: true },
  ]);
  assert.equal(await solver().isGeetestAccepted(page), true);
});

test('a REFUSED drag is not a solve', async () => {
  const page = pageShowing([
    { cls: 'geetest_result_tips geetest_fail geetest_showResult', visible: true },
    { cls: 'geetest_captcha geetest_customTheme geetest_freeze_wait geetest_fail', visible: true },
  ]);
  assert.equal(await solver().isGeetestAccepted(page), false);
});

test('an untouched widget is not a solve', async () => {
  const page = pageShowing([
    { cls: 'geetest_result_tips', visible: true },
    { cls: 'geetest_captcha geetest_customTheme', visible: true },
  ]);
  assert.equal(await solver().isGeetestAccepted(page), false);
});

test('the closed popup wrapper does not count, class or no class', async () => {
  const page = pageShowing([
    { cls: 'geetest_popup_wrap geetest_popup geetest_customTheme geetest_lock_success',
      visible: false },
  ]);
  assert.equal(await solver().isGeetestAccepted(page), false);
});

test('a page with no GeeTest on it is not a solve', async () => {
  assert.equal(await solver().isGeetestAccepted(pageShowing([])), false);
});
