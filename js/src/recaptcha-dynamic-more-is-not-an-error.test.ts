import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

function pageShowing(selector: string | null, text = 'banner text'): any {
  const frame = {
    $: async (sel: string) =>
      sel === selector
        ? { isVisible: async () => true, textContent: async () => text }
        : null,
  };
  return {
    $: async (sel: string) =>
      sel === 'iframe[src*="recaptcha/api2/bframe"]'
        ? { contentFrame: async () => frame }
        : null,
  };
}

const solver = () => new CaptchaKrakenSolver({ apiKey: 'test' }) as any;

test('"Please try again" is a rejection', async () => {
  const kind = await solver().recaptchaBannerKind(
    pageShowing('.rc-imageselect-incorrect-response', 'Please try again.'),
  );
  assert.equal(kind, 'rejected');
});

test('"Please select all matching images" is an under-selection', async () => {
  const kind = await solver().recaptchaBannerKind(
    pageShowing('.rc-imageselect-error-select-more', 'Please select all matching images.'),
  );
  assert.equal(kind, 'select-more');
});

test('"Please also check the new images" is PROGRESS, not an error', async () => {
  const kind = await solver().recaptchaBannerKind(
    pageShowing('.rc-imageselect-error-dynamic-more', 'Please also check the new images.'),
  );
  assert.equal(
    kind,
    'dynamic-more',
    'the dynamic variant\'s "more images arrived" notice must not be classified with the two ' +
      'genuine errors — doing so aborts every dynamic board at round two',
  );
});

test('a clean board reports no banner', async () => {
  assert.equal(await solver().recaptchaBannerKind(pageShowing(null)), null);
});

test('dynamic-more never arms the abort latch', async () => {
  const s = solver();
  const page = pageShowing('.rc-imageselect-error-dynamic-more', 'Please also check the new images.');
  assert.equal(s.bannerIsFatalAfterRetry(await s.recaptchaBannerKind(page)), false);
  assert.equal(s.bannerIsFatalAfterRetry(await s.recaptchaBannerKind(page)), false);
});

test('a repeated under-selection IS fatal after one retry', async () => {
  const s = solver();
  const page = pageShowing('.rc-imageselect-error-select-more', 'Please select all matching images.');
  assert.equal(s.bannerIsFatalAfterRetry(await s.recaptchaBannerKind(page)), true);
});
