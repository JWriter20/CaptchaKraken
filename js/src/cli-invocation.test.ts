/**
 * The bearer token must reach the CLI through the environment, and must never
 * appear in argv or in anything the client prints.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  API_KEY_ENV,
  RESAMPLE_LEVEL_ENV,
  buildSolveArgs,
  redactCommand,
  solveEnv,
} from './cli-invocation';

// A synthetic token. Every assertion here is about where the string
// travels, so the fixture is generated rather than read from anywhere.
const KEY = `deadbeef${'0123456789abcdef'.repeat(3)}deadbeef`;

const invocation = {
  imagePath: '/tmp/captcha_123.png',
  model: 'captcha-v12',
  puzzleSource: 'hcaptcha',
};

test('the api key never appears in argv', () => {
  const args = buildSolveArgs(invocation);
  for (const arg of args) {
    assert.ok(
      !arg.includes(KEY),
      `argv carried the credential (${arg}). /proc/<pid>/cmdline is world-readable.`,
    );
  }
});

test('the key is passed through the environment instead', () => {
  const env = solveEnv({ PATH: '/usr/bin' }, KEY);
  assert.equal(env[API_KEY_ENV], KEY);
  assert.equal(env.PATH, '/usr/bin', 'the base environment must survive');
});

test('no credential in the environment when none was configured', () => {
  const env = solveEnv({ PATH: '/usr/bin' });
  assert.ok(!(API_KEY_ENV in env));
});

test('a logged command is redacted even if a key reaches it', () => {
  const line = `python -m captchakraken.cli shot.png captcha-v12 captchaKrakenApi ${KEY}`;
  const redacted = redactCommand(line, KEY);
  assert.ok(!redacted.includes(KEY), 'the printed command still contained the key');
  assert.ok(redacted.includes('***'));
});

test('args are an array for execFile, not a shell string', () => {
  // A joined string would go through /bin/sh, where a screenshot path
  // containing a space or a quote could reshape the command.
  const args = buildSolveArgs({ ...invocation, imagePath: "/tmp/a b'c.png" });
  assert.ok(Array.isArray(args));
  assert.ok(
    args.includes("/tmp/a b'c.png"),
    'the path must be one literal argv entry, not shell-quoted text',
  );
});

test('vendor hint, retry mode and text mode still reach the CLI', () => {
  const args = buildSolveArgs({
    ...invocation,
    retryMode: 'fresh',
    textMode: true,
  });
  assert.ok(args.includes('--puzzle-source=hcaptcha'));
  assert.ok(args.includes('--retry-mode=fresh'));
  assert.ok(args.includes('--text-mode'));
  assert.ok(args.includes('captcha-v12'));
});

test('a re-ask carries its level to the CLI, and a first look does not', () => {
  // The CLI is a fresh process per round, so a level it is not told is a level
  // it does not have: the request would be the same greedy decode over the
  // same pixels, and would return the same answer.
  const first = solveEnv({}, 'k');
  assert.equal(first[RESAMPLE_LEVEL_ENV], undefined,
    'a first look must stay deterministic');
  const reask = solveEnv({}, 'k', 2);
  assert.equal(reask[RESAMPLE_LEVEL_ENV], '2');
  // The LEVEL travels, never the temperature: the schedule lives once, in
  // planner.RESAMPLE_TEMPERATURES, so the two ports cannot drift.
  assert.equal(reask['CAPTCHA_RESAMPLE_TEMPERATURE'], undefined);
});

test('level 0 is not sent at all', () => {
  assert.equal(solveEnv({}, 'k', 0)[RESAMPLE_LEVEL_ENV], undefined);
});
