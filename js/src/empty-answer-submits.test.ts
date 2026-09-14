// Structural on purpose: `none_present` boards read as SKIP, and the regression surfaced with the macOS font fix and read as that fix's fault.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'fs';
import * as path from 'path';

function findSolverSource(): string {
  let dir = __dirname;
  for (let i = 0; i < 6; i++) {
    for (const rel of ['solver.ts', path.join('src', 'solver.ts')]) {
      const candidate = path.join(dir, rel);
      if (fs.existsSync(candidate)) return candidate;
    }
    dir = path.dirname(dir);
  }
  throw new Error(`could not locate solver.ts upward from ${__dirname}`);
}

const SOLVER = findSolverSource();
const LOOKUP = 'getVerifyButton';

function depths(src: string): number[] {
  const out: number[] = [];
  let depth = 0;
  for (const line of src.split('\n')) {
    out.push(depth);

    const bare = line
      .replace(/\/\/.*$/, '')
      .replace(/'(?:[^'\\]|\\.)*'/g, "''")
      .replace(/"(?:[^"\\]|\\.)*"/g, '""')
      .replace(/`(?:[^`\\]|\\.)*`/g, '``');
    for (const ch of bare) {
      if (ch === '{') depth++;
      else if (ch === '}') depth--;
    }
  }
  return out;
}

test('the submit control is resolved outside the loop over the model actions', () => {
  const src = fs.readFileSync(SOLVER, 'utf-8');
  const lines = src.split('\n');
  const depth = depths(src);

  const loopIdx = lines.findIndex((l) => /for\s*\(\s*const\s+action\s+of\s+actionList/.test(l));
  assert.notEqual(loopIdx, -1,
    'no `for (const action of actionList)` in solver.ts — re-point this test at '
    + 'the loop that executes the model plan');

  const loopDepth = depth[loopIdx];

  const callIdxs = lines
    .map((l, i) => ({ l, i }))
    .filter(({ l }) => new RegExp(`this\\.${LOOKUP}\\s*\\(`).test(l))
    .map(({ i }) => i);

  assert.ok(callIdxs.length > 0,
    `this.${LOOKUP}(...) is never called in solver.ts — the widget's own submit `
    + 'control would never be pressed by any path');

  let loopEnd = lines.length;
  for (let i = loopIdx + 1; i < lines.length; i++) {
    if (depth[i] <= loopDepth) { loopEnd = i; break; }
  }

  const nested = callIdxs.filter((i) => i > loopIdx && i < loopEnd);
  assert.deepEqual(nested.map((i) => i + 1), [],
    `this.${LOOKUP}() is called INSIDE the action loop `
    + `(line${nested.length > 1 ? 's' : ''} ${nested.map((i) => i + 1).join(', ')}, `
    + `loop spans ${loopIdx + 1}..${loopEnd}).\n\n`
    + 'A plan with NO actions never enters that loop, so no control is resolved, '
    + 'shouldClickSubmit finds verifyButton null, nothing is pressed and the '
    + "round aborts on 'performed no interactions'. That is reCAPTCHA 3x3's "
    + '`none_present` variation, whose correct answer is to select nothing and '
    + 'press SKIP.\n\n'
    + 'Resolve the control after the loop, on the same level as the submit '
    + 'decision that consumes it.');
});
