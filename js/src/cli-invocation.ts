/**
 * How the JS client hands a solve request to the bundled Python CLI.
 *
 * The API key used to travel as a POSITIONAL ARGUMENT, and the whole command
 * line was then echoed:
 *
 *   console.log(`Executing CaptchaKraken CLI: ${command}`);
 *   // Executing CaptchaKraken CLI: python -m captchakraken.cli "shot.png" \
 *   //   captcha-v12 captchaKrakenApi b8978fa3392...  --puzzle-source=hcaptcha
 *
 * Two leaks from one mistake. Argv is world-readable on Linux — any local user
 * can read `/proc/<pid>/cmdline` while the solve runs, and `ps` shows it — and
 * the key was additionally written to stdout, so it landed in CI logs, terminal
 * scrollback, and anything scraping driver output.
 *
 * Secrets go through the environment instead, which is per-process and not
 * world-readable, and the command is built as an ARGV ARRAY for `execFile`
 * rather than a joined string for a shell, so a path with a space or a quote
 * cannot reshape the command.
 */

/** Env var the CLI reads the bearer token from. */
export const API_KEY_ENV = 'CAPTCHA_KRAKEN_API_KEY';

/** Historical positional placeholder. The CLI still accepts the slot. */
export const API_PROVIDER = 'captchaKrakenApi';

export interface SolveInvocation {
  imagePath: string;
  model: string;
  puzzleSource: string;
  retryMode?: string | null;
  textMode?: boolean;
}

/**
 * The argv for one solve. No shell, no quoting, and NO CREDENTIAL — the key is
 * supplied by `solveEnv` instead.
 */
export function buildSolveArgs(invocation: SolveInvocation): string[] {
  const { imagePath, model, puzzleSource, retryMode, textMode } = invocation;
  const args = [
    '-m',
    'captchakraken.cli',
    imagePath,
    model,
    API_PROVIDER,
    `--puzzle-source=${puzzleSource}`,
  ];
  if (retryMode) args.push(`--retry-mode=${retryMode}`);
  if (textMode) args.push('--text-mode');
  return args;
}

/** Base env plus the credential, when there is one. */
export function solveEnv(
  base: NodeJS.ProcessEnv,
  apiKey?: string,
): NodeJS.ProcessEnv {
  if (!apiKey) return base;
  return { ...base, [API_KEY_ENV]: apiKey };
}

/**
 * A log line for a solve invocation, with any credential removed.
 *
 * Belt and braces: `buildSolveArgs` no longer carries the key, but this is the
 * function that prints to stdout, so it redacts anything key-shaped regardless
 * of how it got into the array.
 */
export function redactCommand(command: string, apiKey?: string): string {
  if (!apiKey) return command;
  return command.split(apiKey).join('***');
}
