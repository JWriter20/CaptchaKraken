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

  humanization_modes: (Array.isArray(MODES) ? [...MODES] : Object.keys(MODES)).sort(),
  config_fields: interfaceFields('types.ts', 'CaptchaKrakenConfig'),
  solve_result_fields: interfaceFields('types.ts', 'SolveResult'),
  // Read off the source, not the runtime object, so a code that stops being a literal is caught here.
  error_codes: [
    ...readFileSync(resolve(ROOT, 'js', 'src', 'kinds.ts'), 'utf8')
      .slice(readFileSync(resolve(ROOT, 'js', 'src', 'kinds.ts'), 'utf8').indexOf('export const ErrorCode'))
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
  assert.deepEqual(live.humanization_modes, stored.python.humanization_modes);
});

test('both ports know the same API error codes', () => {
  assert.deepEqual(
    live.error_codes.filter((c) => stored.python.error_codes.includes(c)).length,
    stored.python.error_codes.length,
    `Python knows ${JSON.stringify(stored.python.error_codes)}; JS knows ` +
      `${JSON.stringify(live.error_codes)}. Every code one port branches on, ` +
      `the other must too.`,
  );
});

test('the two ports name the same solver options', () => {
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
