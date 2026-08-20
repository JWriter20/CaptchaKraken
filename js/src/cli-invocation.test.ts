/**
 * Regression: the bearer token must never reach argv or stdout.
 *
 * Seen in a real driver run — the key in the process table AND echoed to the
 * console:
 *
 *   Executing CaptchaKraken CLI: python -m captchakraken.cli "shot.png" \
 *     captcha-v12 captchaKrakenApi b8978fa3392...  --puzzle-source=hcaptcha
 *
 * On Linux `/proc/<pid>/cmdline` is world-readable, so any local user could
 * read the key for as long as the solve ran, and the same string went into
 * stdout, CI logs and scrollback.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  API_KEY_ENV,
  buildSolveArgs,
  redactCommand,
  solveEnv,
} from './cli-invocation';

const KEY = 'b8978fa339207eee2ebcc15d5bb3b594fb39ac71c4be1ae1581f3834d3563000';

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
  // A joined string went through `exec`, i.e. through /bin/sh. A screenshot
  // path containing a space or a quote could then reshape the command.
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
