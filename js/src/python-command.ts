/**
 * Which interpreter runs the bundled CaptchaKraken CLI.
 *
 * This used to be `pythonCommand = 'python'`, inline in solver.ts. On
 * Debian/Ubuntu — the most common Linux deployment target, and what CI runs —
 * there is no `python` on PATH at all, only `python3`. So every JS solve died
 * with `/bin/sh: 1: python: not found` before it ever reached the model, unless
 * a `.venv` happened to exist next to the package. The type doc claimed the
 * default was "'python' or 'python3'"; the code never tried `python3`.
 *
 * That is a straight violation of the rule that the Python and JS clients
 * behave identically: the Python client obviously ran, so the divergence looked
 * like a model or endpoint problem rather than a missing binary.
 *
 * Kept in its own module, with the probe injected, so the resolution order is
 * testable without spawning anything.
 */

/** Env var that overrides everything below it. */
export const PYTHON_ENV_VAR = 'CAPTCHA_KRAKEN_PYTHON';

/**
 * Candidates tried in order when nothing is configured.
 *
 * `python3` FIRST. On Debian-family systems `python` is absent; where both
 * exist they are the same interpreter in practice, and PEP 394 makes `python3`
 * the portable spelling. `python` stays as the fallback for Windows and for
 * venvs that only expose the unsuffixed name.
 */
export const PYTHON_CANDIDATES = ['python3', 'python'] as const;

export interface ResolveOptions {
  /** `config.pythonCommand`, if the caller set one. Wins outright. */
  configured?: string;
  /** The venv interpreter, when one was found next to the CLI. Wins over probing. */
  venvPython?: string | null;
  /** Process env, injected for testability. */
  env?: NodeJS.ProcessEnv;
  /** Returns true if the command is runnable. Injected so tests never spawn. */
  exists?: (command: string) => boolean;
}

/**
 * Resolve the interpreter, most specific first:
 *
 *   1. an explicitly configured `pythonCommand`
 *   2. `CAPTCHAKRAKEN_PYTHON`
 *   3. the venv interpreter beside the bundled CLI
 *   4. the first of `python3`, `python` that actually exists
 *
 * Falls back to `python3` rather than `python` when nothing can be probed: on
 * the platform where the two differ, `python3` is the one that is present.
 */
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
