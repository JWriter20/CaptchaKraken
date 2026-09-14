/**
 * GeeTest's accepted state is neither a token nor an absence.
 *
 * The verdict loop after an action knows three positive signals — an hCaptcha,
 * reCAPTCHA or Turnstile response token — and otherwise falls back on "the
 * widget is gone". GeeTest does neither when it accepts a drag: it paints a
 * result banner INSIDE the still-open panel and closes some seconds later. So
 * the answer is accepted, on screen, while every check the loop makes says no;
 * the 1s window expires; and the next round spends a WHOLE INFERENCE finding
 * out the puzzle was already solved.
 *
 * MEASURED on gt4.geetest.com's slide demo, 2026-09-12, three consecutive live
 * solves — the driver opened another solve loop after the banner had painted,
 * every time:
 *
 *     run 1   t+12873ms success  ->  "Captcha Solve Loop 3/6"
 *     run 2   t+25418ms success  ->  "Captcha Solve Loop 5/6"
 *     run 3   t+12571ms success  ->  "Captcha Solve Loop 3/6"
 *
 * Over ten filmed attempts: 34 model calls for 20 drags, and ~6s between the
 * winning drag and the solve being reported.
 *
 * The Python half is python/tests/test_geetest_accept_is_a_signal.py.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

/** A page whose `$$` returns handles for the classes GeeTest has painted. */
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
  // The vendor's own discriminator: same element, `geetest_fail`. Reading this
  // as a solve would report every miss as a pass.
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
  /*
   * `geetest_popup_wrap` carries the success class at ZERO HEIGHT while the
   * panel is shut, and sits before the real banner in document order. A bare
   * class match — or a `$` that takes the first hit — reports a solve on a
   * widget nobody has touched.
   */
  const page = pageShowing([
    { cls: 'geetest_popup_wrap geetest_popup geetest_customTheme geetest_lock_success',
      visible: false },
  ]);
  assert.equal(await solver().isGeetestAccepted(page), false);
});

test('a page with no GeeTest on it is not a solve', async () => {
  assert.equal(await solver().isGeetestAccepted(pageShowing([])), false);
});
