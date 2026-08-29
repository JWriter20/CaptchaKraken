/**
 * Resolving the interpreter must not be paid per inference.
 *
 * `resolveCli()` ends in `resolvePythonCommand`, which — with nothing
 * configured and no bundled venv, i.e. the plain `npm install` default —
 * PROBES the interpreter with `spawnSync(cmd, ['--version'])`. spawnSync
 * blocks the entire Node event loop: Playwright's socket pump, every timer,
 * the humanizer's own pacing sleeps, all of it, for the length of a process
 * start.
 *
 * It was called on every model call (`askModel`, `getAnimatedSolution`), every
 * one-shot CV fallback and every CV-worker start — up to two blocking spawns
 * per inference, to re-derive an answer that cannot change inside one process.
 * `resolveLoraName` sat next to it doing a synchronous `readFileSync` of
 * models.json, per inference, for the same reason.
 *
 * There is no way to observe a blocked event loop from a unit test, so what is
 * pinned here is the property that makes it impossible: the resolution happens
 * once and every later caller is handed the same answer back.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { CaptchaKrakenSolver } from './solver';

test('the interpreter is resolved once per solver', () => {
  const s: any = new CaptchaKrakenSolver({ pythonCommand: process.execPath });
  const first = s.resolveCli();
  assert.strictEqual(s.resolveCli(), first,
    'resolveCli() re-derived its answer — with no bundled venv that is a '
    + 'blocking `python --version` spawn on the inference hot path');
});

test('the adapter name is read off disk once per solver', () => {
  const s: any = new CaptchaKrakenSolver({ pythonCommand: process.execPath });
  const { cliRoot } = s.resolveCli();
  const first = s.loraName(cliRoot);

  assert.equal(s.loraName(cliRoot), first);
  assert.equal(s.loraNameCache, first,
    'the resolved adapter name is not cached, so models.json is re-read '
    + 'synchronously on every inference');
});
