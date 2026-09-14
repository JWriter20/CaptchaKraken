import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const SOLVER = readFileSync(path.join(__dirname, '..', 'src', 'solver.ts'), 'utf8');

test('every element screenshot goes through shot(), which always bounds the timeout', () => {
  // Playwright's 30s default hangs on a challenge that is being torn down.
  assert.equal([...SOLVER.matchAll(/\.screenshot\(/g)].length, 1, 'a screenshot call bypasses shot()');
  assert.match(SOLVER, /private shot\(el: ElementHandle, p: string, timeout = 2500[^)]*\)[^{]*\{\s*return el\.screenshot\(\{ path: p, timeout, animations \}\);/);
});
