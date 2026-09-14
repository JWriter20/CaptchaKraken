import fs from 'node:fs';
import path from 'node:path';

export function getBundledCliRoot(): string {
  const bundled = path.resolve(__dirname, '..', 'python');
  if (fs.existsSync(bundled)) return bundled;
  return path.resolve(__dirname, '..', '..', 'python');
}

function readJson(cliRoot: string, name: string): any {
  return JSON.parse(
    fs.readFileSync(path.join(cliRoot, 'src', 'captchakraken', name), 'utf-8'));
}

// An exact host list, not "is it remote": a self-hoster's vLLM across the network is remote too, and the hosted-only model 404s there.
const HOSTED_HOSTS = ['api.captchakraken.com'];

export function isHostedEndpoint(baseUrl: string | undefined,
                                 env: NodeJS.ProcessEnv = process.env): boolean {
  if (!baseUrl) return false;
  const extra = (env.CAPTCHA_HOSTED_HOSTS ?? '')
    .split(',').map((h) => h.trim().toLowerCase()).filter(Boolean);
  try {
    return [...HOSTED_HOSTS, ...extra].includes(new URL(baseUrl).hostname.toLowerCase());
  } catch {
    return false;
  }
}

/** Hosted: the routing alias, not an arm's `lora_name`, because a routed mixture is several names and only the alias routes. A missing or broken registry falls through to the pin, never throws. */
export function resolveLoraName(
  { cliRoot = getBundledCliRoot(), env = process.env, baseUrl }:
    { cliRoot?: string; env?: NodeJS.ProcessEnv; baseUrl?: string } = {},
): string {
  if (env.CAPTCHA_LORA_NAME) return env.CAPTCHA_LORA_NAME;
  try {
    const reg = readJson(cliRoot, 'models.json');
    if (isHostedEndpoint(baseUrl, env)) {
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
  }
  return readJson(cliRoot, 'pinned_model.json').lora_name;
}
