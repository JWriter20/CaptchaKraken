import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  PYTHON_CANDIDATES,
  PYTHON_ENV_VAR,
  resolvePythonCommand,
} from './python-command';

const debianLike = (command: string) => command === 'python3';

test('falls back to python3 on a system with no bare `python`', () => {
  const resolved = resolvePythonCommand({ env: {}, exists: debianLike });
  assert.equal(
    resolved,
    'python3',
    'A system with only python3 must resolve to python3. Resolving to `python` is ' +
      'the bug that made every JS solve fail with "python: not found".',
  );
});

test('python3 is tried before python', () => {
  assert.equal(
    PYTHON_CANDIDATES[0],
    'python3',
    'PEP 394: python3 is the portable spelling, and the only one present on Debian.',
  );
});

test('still uses `python` when that is the only interpreter present', () => {
  const windowsLike = (command: string) => command === 'python';
  assert.equal(resolvePythonCommand({ env: {}, exists: windowsLike }), 'python');
});

test('an explicit pythonCommand always wins', () => {
  const resolved = resolvePythonCommand({
    configured: '/opt/venv/bin/python',
    venvPython: '/somewhere/.venv/bin/python',
    env: { [PYTHON_ENV_VAR]: '/env/python' },
    exists: debianLike,
  });
  assert.equal(resolved, '/opt/venv/bin/python');
});

test(`${PYTHON_ENV_VAR} overrides discovery`, () => {
  const resolved = resolvePythonCommand({
    env: { [PYTHON_ENV_VAR]: '/env/python' },
    venvPython: '/somewhere/.venv/bin/python',
    exists: debianLike,
  });
  assert.equal(resolved, '/env/python');
});

test('the bundled venv interpreter beats a bare PATH lookup', () => {
  const resolved = resolvePythonCommand({
    venvPython: '/pkg/python/.venv/bin/python',
    env: {},
    exists: debianLike,
  });
  assert.equal(resolved, '/pkg/python/.venv/bin/python');
});

test('resolves to python3, not python, when nothing can be probed', () => {
  assert.equal(resolvePythonCommand({ env: {} }), 'python3');
});
