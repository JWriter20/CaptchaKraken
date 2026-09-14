export const API_KEY_ENV = 'CAPTCHA_KRAKEN_API_KEY';

export const RESAMPLE_LEVEL_ENV = 'CAPTCHA_RESAMPLE_LEVEL';

export const API_PROVIDER = 'captchaKrakenApi';

export interface SolveInvocation {
  imagePath: string;

  model?: string;
  puzzleSource: string;
  retryMode?: string | null;
  textMode?: boolean;

  expert?: string | null;
}

export function buildSolveArgs(invocation: SolveInvocation): string[] {
  const { imagePath, model, puzzleSource, retryMode, textMode, expert } =
    invocation;

  const args = ['-m', 'captchakraken.cli', imagePath];
  if (model) args.push(model, API_PROVIDER);
  args.push(`--puzzle-source=${puzzleSource}`);
  if (retryMode) args.push(`--retry-mode=${retryMode}`);
  if (textMode) args.push('--text-mode');

  if (expert) args.push(`--expert=${expert}`);
  return args;
}

export function solveEnv(
  base: NodeJS.ProcessEnv,
  apiKey?: string,
  resampleLevel?: number,
): NodeJS.ProcessEnv {
  const out: NodeJS.ProcessEnv = apiKey ? { ...base, [API_KEY_ENV]: apiKey } : { ...base };

  if (resampleLevel && resampleLevel > 0) {
    out[RESAMPLE_LEVEL_ENV] = String(resampleLevel);
  }
  return out;
}

export function redactCommand(command: string, apiKey?: string): string {
  if (!apiKey) return command;
  return command.split(apiKey).join('***');
}
