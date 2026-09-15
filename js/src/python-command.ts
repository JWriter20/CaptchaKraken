export const PYTHON_ENV_VAR = 'CAPTCHA_KRAKEN_PYTHON';

export const PYTHON_CANDIDATES = ['python3', 'python'] as const;

export interface ResolveOptions {
  configured?: string;

  venvPython?: string | null;

  env?: NodeJS.ProcessEnv;

  exists?: (command: string) => boolean;
}

export function resolvePythonCommand(options: ResolveOptions = {}): string {
  const { configured, venvPython, env = process.env, exists } = options;

  if (configured) return configured;

  const fromEnv = env[PYTHON_ENV_VAR];
  if (fromEnv) return fromEnv;

  if (venvPython) return venvPython;

  if (exists) {
    for (const candidate of PYTHON_CANDIDATES) {
      if (exists(candidate)) return candidate;
    }
  }

  return PYTHON_CANDIDATES[0];
}
