/**
 * The JS port must ask for the SAME model the Python port does.
 *
 * It did not. `solver.ts` defaulted to a hardcoded `'captcha'` — the served
 * alias of CaptchaKraken_v1.1, a GENERATION 1 model — while the Python client
 * resolved `models.json`'s `latest` to `captcha-v12` (generation 2). Since the
 * JS port answers by shelling out to the Python CLI and passing that name as
 * `--model`, the name it picks is what `prompts.resolve()` maps to a prompt
 * generation. So every JS solve ran generation-1 prompts against generation-2
 * weights: the exact mispairing models.json exists to prevent, and silent for
 * every family that generation 1 still has a prompt for.
 *
 * It was not silent for the two families generation 1 does NOT have: Tier 3 run
 * 2026-08-20 lost 19 puzzle types to `prompt generation 1 has no animated-puzzle
 * prompt` and 3 more (botdetect_text, mtcaptcha_text, yandex_text) to the
 * distorted-text equivalent surfacing as UNSUPPORTED_CAPTCHA — 22 of the port's
 * 44 types, against a python port that scored 37/44 locally on the same commit.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

import { resolveLoraName } from './model-name.js';

const CLI_ROOT = path.resolve(__dirname, '..', '..', 'python');
const registry = () => JSON.parse(
  fs.readFileSync(path.join(CLI_ROOT, 'src', 'captchakraken', 'models.json'), 'utf-8'));

test('defaults to the registry `latest` entry, not a hardcoded alias', () => {
  const reg = registry();
  const expected = reg.models[reg.latest].lora_name;
  assert.equal(resolveLoraName({ cliRoot: CLI_ROOT, env: {} }), expected);
});

test('the default is a generation-2 model — v1 has no video or text prompt', () => {
  const reg = registry();
  const name = resolveLoraName({ cliRoot: CLI_ROOT, env: {} });
  const repoId = reg.served_aliases[name] ?? name;
  assert.equal(reg.models[repoId].prompt_version, '2');
});

test('finds the bundled engine on its own — cliRoot is optional', () => {
  // The form a reporter uses. Without it, anything that wants to RECORD the
  // name has to rebuild the engine's path layout itself — a third copy of the
  // decision, which is how the ports drifted apart to begin with.
  //
  // `env: {}` only so an ambient CAPTCHA_LORA_NAME cannot decide the result;
  // what is under test is the DEFAULTED cliRoot, and it must find the same
  // registry the explicit-root cases above read.
  const reg = registry();
  assert.equal(resolveLoraName({ env: {} }), reg.models[reg.latest].lora_name);
  assert.equal(resolveLoraName({ env: { CAPTCHA_LORA_NAME: 'pinned' } }), 'pinned');
});

test('CAPTCHA_LORA_NAME still wins — pinning stays opt-in', () => {
  assert.equal(
    resolveLoraName({ cliRoot: CLI_ROOT, env: { CAPTCHA_LORA_NAME: 'captcha' } }),
    'captcha');
});

test('falls back to pinned_model.json when the registry is unreadable', () => {
  // A root that has the pin but no registry — a hand-edited or older install.
  // The point is that it still resolves rather than reverting to a literal.
  const pinned = JSON.parse(
    fs.readFileSync(path.join(CLI_ROOT, 'src', 'captchakraken', 'pinned_model.json'), 'utf-8'));
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ck-model-name-'));
  fs.mkdirSync(path.join(root, 'src', 'captchakraken'), { recursive: true });
  fs.writeFileSync(path.join(root, 'src', 'captchakraken', 'pinned_model.json'),
    JSON.stringify(pinned));
  assert.equal(resolveLoraName({ cliRoot: root, env: {} }), pinned.lora_name);
});
