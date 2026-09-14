import { test } from 'node:test';
import assert from 'node:assert/strict';

import { buildSolveArgs } from './cli-invocation';

const base = {
  imagePath: '/tmp/board.png',
  model: 'captcha-v12',
  puzzleSource: 'hcaptcha',
};

test('a pinned expert is forwarded to the CLI', () => {
  assert.ok(buildSolveArgs({ ...base, expert: 'grid' }).includes('--expert=grid'));
});

test('an unset expert sends no flag, so Python routes by prompt family', () => {
  for (const expert of [undefined, null, '']) {
    const args = buildSolveArgs({ ...base, expert } as never);
    assert.equal(args.some((a) => a.startsWith('--expert')), false);
  }
});

test('pinning an expert moves nothing else on the command line', () => {
  const without = buildSolveArgs(base);
  const withPin = buildSolveArgs({ ...base, expert: 'video' });
  assert.deepEqual(withPin.slice(0, without.length), without);
  assert.equal(withPin.length, without.length + 1);
});

test('the credential is still nowhere in argv', () => {
  const args = buildSolveArgs({ ...base, expert: 'text' });
  assert.equal(args.join(' ').includes('Bearer'), false);
});
