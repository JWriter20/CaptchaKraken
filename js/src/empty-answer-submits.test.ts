/**
 * An empty answer is an answer, and it still has to be sent.
 *
 * Regression: the submit-control lookup lived INSIDE the loop over the model's
 * actions, while the decision to press it lived outside. A plan with no actions
 * never entered that loop, so `verifyButton` stayed null, and
 *
 *     const shouldClickSubmit = !slid && (answered || !performedAction);
 *     if (shouldClickSubmit && verifyButton) { ... }
 *
 * computed `shouldClickSubmit === true` — the branch that exists precisely for
 * "we had nothing to do and want the round to advance" — and then pressed
 * nothing, because the control it needed had never been resolved.
 * `performedAction` stayed false and the caller aborted the round on
 * "Captcha still detected but solver performed no interactions".
 *
 * WHERE IT BIT, AND WHY IT LOOKED LIKE A MODEL REGRESSION
 *
 * reCAPTCHA's 3x3 has a `none_present` variation: the prompt names a class, no
 * tile contains it, and the widget's control reads SKIP rather than VERIFY. The
 * correct answer is to select nothing and press it. Fixture seed 20260730 is
 * exactly that — target `traffic light`, `target_ids: []`, `submit_label: SKIP`.
 *
 * It surfaced when CaptchaKrakenFinetune fixed font resolution on the macOS
 * Tier 3 runner. Before, the prompt rendered in a fallback bitmap face reading
 * "Selectall images with / traffic lights"; after, real reCAPTCHA chrome with
 * the target term bolded and a legible "If there are none, click skip." This
 * client runs at temperature 0, so the model is a function of the picture: a
 * correct picture got a correct EMPTY answer, and the empty answer was the one
 * shape the driver could not send. `recaptcha_grid_3x3` js went 2/3 -> 1/3 and
 * read as the font fix causing a regression.
 *
 * `getVerifyButton` was never the problem — 'Skip' has always been in its list
 * (see geetest-submit-button.test.ts, which pins the finder itself). The finder
 * was simply never called.
 *
 * This is a STRUCTURAL test. What is wrong is where a call sits relative to a
 * loop, and `solveSingle` is 200 lines around a screenshot, a planner
 * round-trip and a live page — mocking all of that observes the nesting far
 * less directly than reading it. The Python half is pinned by
 * `python/tests/test_empty_answer_still_submits.py`; per CLAUDE.md 1c the two
 * ports must behave the same.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'fs';
import * as path from 'path';

/**
 * `npm test` compiles into `.test-build/`, so `__dirname` is not the source
 * tree. Walk up for `src/solver.ts` and this works under both tsx (run from
 * `src/`) and the compiled runner.
 */
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

/** Brace depth at the START of each line, ignoring braces inside strings. */
function depths(src: string): number[] {
  const out: number[] = [];
  let depth = 0;
  for (const line of src.split('\n')) {
    out.push(depth);
    // Strip line comments and string/template literals before counting, so a
    // brace inside an xpath template does not shift the depth.
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

  // Where the finder is CALLED (not declared, not referenced in a comment).
  const callIdxs = lines
    .map((l, i) => ({ l, i }))
    .filter(({ l }) => new RegExp(`this\\.${LOOKUP}\\s*\\(`).test(l))
    .map(({ i }) => i);

  assert.ok(callIdxs.length > 0,
    `this.${LOOKUP}(...) is never called in solver.ts — the widget's own submit `
    + 'control would never be pressed by any path');

  // A call belongs to the loop if it sits deeper than the loop's own line and
  // before the loop closes (the first line back at loopDepth).
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
