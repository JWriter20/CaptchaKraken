/**
 * The JS half of ../../contract.json, and the parity between the two ports.
 *
 * Project rule 1c: the Python and TypeScript clients must behave the same. Until
 * now nothing checked it. No test imported `src/index.ts` at all, so the export
 * list, `package.json`'s `main`/`types`/`postinstall`, and every field name in
 * `CaptchaKrakenConfig` could be renamed and `npm test` stayed green — while the
 * Python suite, equally, never looked at its own `__all__`.
 *
 * What the parity block below found on its first run is the argument for its
 * existence: the two ports disagree on names that are not a snake_case↔camelCase
 * transform (`max_unsupported_resolves` vs `maxUnsupportedReSolves`), on the
 * shape of `SolveResult.tokenUsage`, and on which slider knobs exist at all.
 * Those are pinned as KNOWN, not fixed here — fixing them is a breaking change
 * to one port or the other and belongs in its own commit with a version bump.
 * Pinning them means the list can only shrink deliberately, and a NEW divergence
 * fails the build.
 *
 * Regenerate after an intended change:
 *     CONTRACT_WRITE=1 npm test
 * and commit the ../../contract.json diff — that diff is the deprecation note.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

import * as pkg from './index';
import { MODES } from './humanize';

const ROOT = resolve(__dirname, '..', '..');
const CONTRACT = resolve(ROOT, 'contract.json');
const stored = JSON.parse(readFileSync(CONTRACT, 'utf8'));

const packageJson = JSON.parse(readFileSync(resolve(ROOT, 'js', 'package.json'), 'utf8'));
const mcpJson = JSON.parse(readFileSync(resolve(ROOT, 'mcp', 'package.json'), 'utf8'));

/** Field names of a TS interface, read out of the source. Types are erased at
 *  runtime, and these names ARE the contract — a caller writes them by hand. */
function interfaceFields(file: string, name: string): string[] {
  const src = readFileSync(resolve(ROOT, 'js', 'src', file), 'utf8');
  const start = src.indexOf(`export interface ${name} {`);
  assert.notEqual(start, -1, `${name} is gone from ${file}`);
  let depth = 0;
  let i = src.indexOf('{', start);
  const open = i;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) break;
  }
  const body = src.slice(open + 1, i);
  // Top-level members only: `  name?: type;` at exactly two spaces of indent.
  return [...body.matchAll(/^ {2}([A-Za-z_][A-Za-z0-9_]*)\??\s*:/gm)]
    .map((m) => m[1])
    .sort();
}

const live = {
  package: {
    name: packageJson.name,
    main: packageJson.main,
    types: packageJson.types,
    bin: packageJson.bin ?? null,
    files: packageJson.files,
    postinstall: packageJson.scripts.postinstall,
  },
  exports: Object.keys(pkg).sort(),
  // MODES is an ARRAY here and a dict in Python — a real divergence, harmless
  // because both are only ever read as a set of names, and pinned as such.
  humanization_modes: (Array.isArray(MODES) ? [...MODES] : Object.keys(MODES)).sort(),
  config_fields: interfaceFields('types.ts', 'CaptchaKrakenConfig'),
  solve_result_fields: interfaceFields('types.ts', 'SolveResult'),
  error_codes: [
    ...readFileSync(resolve(ROOT, 'js', 'src', 'errors.ts'), 'utf8')
      .slice(
        readFileSync(resolve(ROOT, 'js', 'src', 'errors.ts'), 'utf8').indexOf(
          'export type CaptchaKrakenErrorCode',
        ),
      )
      .split(';')[0]
      .matchAll(/'([a-z_]+)'/g),
  ]
    .map((m) => m[1])
    .sort(),
  mcp_package: { name: mcpJson.name, bin: mcpJson.bin, main: mcpJson.main ?? null },
};

if (process.env.CONTRACT_WRITE === '1') {
  writeFileSync(CONTRACT, JSON.stringify({ ...stored, js: live }, null, 2) + '\n');
  console.log(`wrote ${CONTRACT}`);
}

test('the published JS surface has not moved', () => {
  assert.deepEqual(
    live,
    stored.js,
    'the JS half of the public contract changed. If that is intended, run ' +
      '`CONTRACT_WRITE=1 npm test` in the same commit and bump the version the ' +
      'change deserves; if not, put the name back — an alias beside the new one ' +
      'costs nothing and keeps every published integration working.',
  );
});

test('both ports offer the same humanization modes', () => {
  // The one config value a caller passes as a literal string in both languages.
  assert.deepEqual(live.humanization_modes, stored.python.humanization_modes);
});

test('both ports know the same API error codes', () => {
  // These arrive from the gateway. A code either port cannot name is a refusal
  // one of them will report as an unexplained failure.
  assert.deepEqual(
    live.error_codes.filter((c) => stored.python.error_codes.includes(c)).length,
    stored.python.error_codes.length,
    `Python knows ${JSON.stringify(stored.python.error_codes)}; JS knows ` +
      `${JSON.stringify(live.error_codes)}. Every code one port branches on, ` +
      `the other must too.`,
  );
});

test('the two ports name the same solver options', () => {
  // snake_case -> camelCase, with the exceptions pinned. The exceptions list is
  // the point: each entry is a real divergence that a caller trips over, and it
  // may only shrink.
  const camel = (s: string) => s.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
  const jsFields = new Set(live.config_fields);
  const pyFields: string[] = stored.python.page_solver_config_fields;

  const missingInJs = pyFields.filter((f) => !jsFields.has(camel(f)));
  const known: string[] = stored.parity.python_only_config;
  assert.deepEqual(
    missingInJs.sort(),
    [...known].sort(),
    'a Python solver option has no TypeScript twin (or a known divergence was ' +
      'fixed — shrink parity.python_only_config in contract.json). Rule 1c: the ' +
      'two ports must behave the same, and a knob that exists in one is a ' +
      'behaviour the other cannot reproduce.',
  );

  const pySet = new Set(pyFields.map(camel));
  const extraInJs = live.config_fields.filter((f) => !pySet.has(f));
  assert.deepEqual(extraInJs.sort(), [...stored.parity.js_only_config].sort());
});
