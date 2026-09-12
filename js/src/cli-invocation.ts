/**
 * How the JS client hands a solve request to the bundled Python CLI.
 *
 * Two rules govern this module, and both exist because the alternative is
 * unsafe rather than merely untidy:
 *
 * 1. The API key travels in the ENVIRONMENT, never in argv and never to stdout.
 *    Argv is world-readable on Linux — any local user can read
 *    `/proc/<pid>/cmdline` while a solve runs, and `ps` shows it — and anything
 *    printed alongside the command lands in CI logs and terminal scrollback.
 *    An environment block is per-process and not world-readable.
 *
 * 2. The command is built as an ARGV ARRAY for `execFile`, not a joined string
 *    for a shell, so a screenshot path containing a space or a quote cannot
 *    reshape the command.
 */

/** Env var the CLI reads the bearer token from. */
export const API_KEY_ENV = 'CAPTCHA_KRAKEN_API_KEY';

/** Env var carrying the re-ask level for a board the vendor already refused. */
export const RESAMPLE_LEVEL_ENV = 'CAPTCHA_RESAMPLE_LEVEL';

/** Positional placeholder the CLI still accepts in this slot. */
export const API_PROVIDER = 'captchaKrakenApi';

export interface SolveInvocation {
  imagePath: string;
  /** Omit to let the CLI resolve it — see `modelName` in solver.ts. */
  model?: string;
  puzzleSource: string;
  retryMode?: string | null;
  textMode?: boolean;
  /** One expert of a routed model, or undefined to route by prompt family. */
  expert?: string | null;
}

/**
 * The argv for one solve. No shell, no quoting, and NO CREDENTIAL — the key is
 * supplied by `solveEnv` instead.
 */
export function buildSolveArgs(invocation: SolveInvocation): string[] {
  const { imagePath, model, puzzleSource, retryMode, textMode, expert } =
    invocation;
  // BOTH POSITIONALS OR NEITHER. `model` and `api_provider` are consecutive
  // optional positionals, so passing the provider without the model binds the
  // provider's value to `model`. Dropping both is safe: `api_provider` defaults
  // to the only value it accepts, and an absent `model` is what lets the CLI
  // resolve the name itself — which it must, because it reads an endpoint (the
  // credentials file) this port cannot see. See `modelName` in solver.ts.
  const args = ['-m', 'captchakraken.cli', imagePath];
  if (model) args.push(model, API_PROVIDER);
  args.push(`--puzzle-source=${puzzleSource}`);
  if (retryMode) args.push(`--retry-mode=${retryMode}`);
  if (textMode) args.push('--text-mode');
  // Only when set. An absent flag is what lets the Python side route by prompt
  // family, and passing `--expert=` would be an empty choice rather than none.
  if (expert) args.push(`--expert=${expert}`);
  return args;
}

/** Base env plus the credential, when there is one. */
export function solveEnv(
  base: NodeJS.ProcessEnv,
  apiKey?: string,
  resampleLevel?: number,
): NodeJS.ProcessEnv {
  const out: NodeJS.ProcessEnv = apiKey ? { ...base, [API_KEY_ENV]: apiKey } : { ...base };
  // How many times THIS board has already been read and refused. The CLI is a
  // fresh process per round, so it cannot know; and without it a re-ask is the
  // same greedy arithmetic on the same pixels and returns the same answer,
  // which is the whole of the "same answer 3 times running" failure.
  //
  // The LEVEL travels, not the temperature: the schedule lives once, in
  // `planner.RESAMPLE_TEMPERATURES`, so the two ports cannot drift apart on a
  // number Tier 3 would then average.
  if (resampleLevel && resampleLevel > 0) {
    out[RESAMPLE_LEVEL_ENV] = String(resampleLevel);
  }
  return out;
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
