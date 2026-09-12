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
 *   2. models.json's `hosted_default`, but ONLY against our own endpoint. That
 *      model is hosted-only: our API serves it and nobody can download it, so
 *      naming it at someone else's vLLM is a 404 rather than an upgrade.
 *   3. models.json's `latest` entry, so the default model and the default
 *      prompt generation move forward together and cannot get out of step.
 *   4. pinned_model.json, so a hand-edited or older manifest still resolves.
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
 * the source development checkout it instead lives at the sibling `../python`.
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
 * Hosts whose endpoint is OURS. An exact host list, not "is it remote": a
 * self-hoster running vLLM on their own network is remote too, and asking THEM
 * for the hosted-only model would 404 every request. Mirrors `_HOSTED_HOSTS`
 * in config.py; extend for staging with CAPTCHA_HOSTED_HOSTS.
 */
const HOSTED_HOSTS = ['api.captchakraken.com'];

export function isHostedEndpoint(baseUrl: string | undefined,
                                 env: NodeJS.ProcessEnv = process.env): boolean {
  if (!baseUrl) return false;
  const extra = (env.CAPTCHA_HOSTED_HOSTS ?? '')
    .split(',').map((h) => h.trim().toLowerCase()).filter(Boolean);
  try {
    return [...HOSTED_HOSTS, ...extra].includes(new URL(baseUrl).hostname.toLowerCase());
  } catch {
    return false;   // a malformed URL is not a hosted one
  }
}

/**
 * `cliRoot` is optional so a caller that only wants to REPORT the name — a test
 * harness recording which adapter it drove — can ask without reconstructing the
 * engine's layout. Re-deriving it is the same mistake one level down.
 *
 * `baseUrl` decides whether the HOSTED default applies. Omit it and it does
 * not: a caller that does not say where it is pointing gets the download
 * default, which is the safe answer for someone else's server.
 */
export function resolveLoraName(
  { cliRoot = getBundledCliRoot(), env = process.env, baseUrl }:
    { cliRoot?: string; env?: NodeJS.ProcessEnv; baseUrl?: string } = {},
): string {
  if (env.CAPTCHA_LORA_NAME) return env.CAPTCHA_LORA_NAME;
  try {
    const reg = readJson(cliRoot, 'models.json');
    if (isHostedEndpoint(baseUrl, env)) {
      // THE ALIAS, not the entry's `lora_name`. A routed mixture is several
      // names and only the alias routes — see `_hosted_default_name` in
      // config.py for the whole argument.
      const repo = reg?.hosted_default;
      if (typeof repo === 'string' && repo) {
        const aliases = Object.entries(reg?.served_aliases ?? {})
          .filter(([a, target]) => target === repo && !a.startsWith('_'))
          .map(([a]) => a);
        const routed = aliases.find((a) => {
          const id = (reg?.served_aliases ?? {})[a];
          return Object.keys(reg?.models?.[id]?.experts ?? {}).length > 0;
        });
        const picked = routed ?? aliases[0] ?? reg?.models?.[repo]?.lora_name;
        if (typeof picked === 'string' && picked) return picked;
      }
    }
    const name = reg?.models?.[reg?.latest]?.lora_name;
    if (typeof name === 'string' && name) return name;
  } catch {
    // A missing or broken registry falls through to the pin, never throws —
    // same contract as config.py's `_registry_default`.
  }
  return readJson(cliRoot, 'pinned_model.json').lora_name;
}
