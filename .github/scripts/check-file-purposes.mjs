#!/usr/bin/env node

import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const MAP = process.argv[2] ?? 'FILE_PURPOSES.md';
const ENTRY = /^\|\s*`([^`]+)`\s*\|/;

function fail(message) {
  console.error(`error: ${message}`);
  process.exit(2);
}

let tracked;
try {
  tracked = execFileSync('git', ['ls-files', '-z'], { encoding: 'utf8' })
    .split('\0')
    .filter(Boolean);
} catch (e) {
  fail(`could not run 'git ls-files': ${e.message}`);
}

let text;
try {
  text = readFileSync(MAP, 'utf8');
} catch (e) {
  fail(`could not read ${MAP}: ${e.message}`);
}

const entries = [];
const duplicates = [];
const seen = new Set();
for (const line of text.split('\n')) {
  const m = ENTRY.exec(line);
  if (!m) continue;
  const path = m[1];
  if (seen.has(path)) duplicates.push(path);
  seen.add(path);
  entries.push(path);
}

const trackedSet = new Set(tracked);
const undocumented = tracked.filter((f) => !seen.has(f)).sort();
const orphaned = entries.filter((p) => !trackedSet.has(p)).sort();

const problems = [];
if (undocumented.length) {
  problems.push(
    `${undocumented.length} tracked file(s) have no entry in ${MAP}:\n` +
      undocumented.map((f) => `    ${f}`).join('\n') +
      `\n  Add one row per file: | \`<path>\` | What it is for. |`,
  );
}
if (orphaned.length) {
  problems.push(
    `${orphaned.length} entr(y/ies) in ${MAP} name a path that is not tracked:\n` +
      orphaned.map((f) => `    ${f}`).join('\n') +
      `\n  The file was deleted, renamed, or is now gitignored — remove or correct the row.`,
  );
}
if (duplicates.length) {
  problems.push(
    `${duplicates.length} path(s) appear in ${MAP} more than once:\n` +
      [...new Set(duplicates)].map((f) => `    ${f}`).join('\n'),
  );
}

if (problems.length) {
  console.error(`${MAP} does not match 'git ls-files'.\n`);
  for (const p of problems) console.error(`  ${p}\n`);
  console.error(
    `  ${tracked.length} tracked file(s), ${entries.length} entr(y/ies) in the map.`,
  );
  process.exit(1);
}

console.log(
  `${MAP}: ${entries.length} entries, ${tracked.length} tracked files, exact match in both directions.`,
);
