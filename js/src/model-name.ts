/**
 * Which served LoRA name this client asks for — the SAME answer the Python
 * client's `config.lora_name()` gives, and for the same reasons.
 *
 * This has to be shared, not re-decided per port. The name is not just routing:
 * the Python CLI feeds it to `prompts.resolve()`, which maps it through
 * models.json to a PROMPT GENERATION. A port that picks a different name sends
 * a different generation of prompts to the same weights, and nothing errors for
 * any family both generations have a prompt for — it just answers worse. That
 * is the failure models.json exists to prevent, and the JS port shipped it:
 * a hardcoded `'captcha'` (CaptchaKraken_v1.1, generation 1) against the
 * generation-2 adapter `latest` has named since v1.2.
 *
 * Precedence mirrors config.py exactly:
 *   1. CAPTCHA_LORA_NAME — an explicit pin always wins, and pinning is opt-in.
 *   2. models.json's `latest` entry, so the default model and the default
 *      prompt generation move forward together and cannot get out of step.
 *   3. pinned_model.json, so a hand-edited or older manifest still resolves.
 */
import fs from 'node:fs';
import path from 'node:path';

/**
 * Where the bundled Python engine lives — the copy of `models.json` this client
 * actually answers from.
 *
 * Lives HERE rather than in solver.ts because the registry lookup below is its
 * only remaining caller-visible use, and a second copy of this path logic is
 * how the two ports drifted apart in the first place. solver.ts imports it.
 *
 * When installed from npm this file is in `<pkgRoot>/dist` (compiled) or
 * `<pkgRoot>/src` (dev), and published packages bundle the engine at
 * `<pkgRoot>/python` (copied in by scripts/copy-python.mjs at build time). In
 * the source monorepo it instead lives at the sibling `../python`.
 */
export function getBundledCliRoot(): string {
  const bundled = path.resolve(__dirname, '..', 'python');
  if (fs.existsSync(bundled)) return bundled;
  return path.resolve(__dirname, '..', '..', 'python');
}

function readJson(cliRoot: string, name: string): any {
  return JSON.parse(
    fs.readFileSync(path.join(cliRoot, 'src', 'captchakraken', name), 'utf-8'));
}

/**
 * `cliRoot` is optional so a caller that only wants to REPORT the name — a test
 * harness recording which adapter it drove — can ask without reconstructing the
 * engine's layout. Re-deriving it is the same mistake one level down.
 */
export function resolveLoraName(
  { cliRoot = getBundledCliRoot(), env = process.env }:
    { cliRoot?: string; env?: NodeJS.ProcessEnv } = {},
): string {
  if (env.CAPTCHA_LORA_NAME) return env.CAPTCHA_LORA_NAME;
  try {
    const reg = readJson(cliRoot, 'models.json');
    const name = reg?.models?.[reg?.latest]?.lora_name;
    if (typeof name === 'string' && name) return name;
  } catch {
    // A missing or broken registry falls through to the pin, never throws —
    // same contract as config.py's `_registry_default`.
  }
  return readJson(cliRoot, 'pinned_model.json').lora_name;
}
