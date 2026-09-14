// The spawnSync probe blocks the event loop and was being paid up to twice per inference before this was memoised.
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
