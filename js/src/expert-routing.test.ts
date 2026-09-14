import { test } from 'node:test';
import assert from 'node:assert/strict';

import { buildSolveArgs, type SolveInvocation } from './cli-invocation';
import { PromptFamily, Vendor } from './kinds';

const base: SolveInvocation = {
  imagePath: '/tmp/board.png',
  model: 'captcha-v12',
  puzzleSource: Vendor.HCAPTCHA,
};

test('a pinned expert is forwarded to the CLI', () => {
  assert.ok(buildSolveArgs({ ...base, expert: PromptFamily.GRID }).includes('--expert=grid'));
});

test('an unset expert sends no flag, so Python routes by prompt family', () => {
  for (const expert of [undefined, null, '']) {
    const args = buildSolveArgs({ ...base, expert } as never);
    assert.equal(args.some((a) => a.startsWith('--expert')), false);
  }
});

test('pinning an expert moves nothing else on the command line', () => {
  const without = buildSolveArgs(base);
  const withPin = buildSolveArgs({ ...base, expert: PromptFamily.VIDEO });
  assert.deepEqual(withPin.slice(0, without.length), without);
  assert.equal(withPin.length, without.length + 1);
});

test('the credential is still nowhere in argv', () => {
  const args = buildSolveArgs({ ...base, expert: PromptFamily.TEXT });
  assert.equal(args.join(' ').includes('Bearer'), false);
});
