/**
 * Hosted-API refusals, carried across the Python subprocess boundary.
 *
 * The TypeScript driver does not talk to the model; it shells out to the Python
 * CLI, which does. So every hosted-API error arrives here as text on stderr and
 * would, without this, be flattened into
 * `Failed to execute captcha solver CLI: Command failed with exit code 3` —
 * a sentence that tells a camoufox user nothing about the fact that their
 * account is out of credits.
 *
 * The CLI exits 3 and prints `{"error": "...", "ck_error": {...}}`, so the
 * wording is decided once, in Python (`captchakraken/errors.py`), and merely
 * relayed here. Deliberately NOT re-worded on this side: two copies of the same
 * message drift, and the Python one is also what a direct CLI user sees.
 */

/** The machine-readable half. Mirrors the gateway's `GatewayErrorCode`. */
export type CaptchaKrakenErrorCode =
  | 'missing_api_key'
  | 'invalid_api_key'
  | 'account_suspended'
  | 'insufficient_credits'
  | 'rate_limited'
  | 'solve_abandoned'
  | 'unrecognized_prompt'
  | 'invalid_request'
  | 'request_too_large'
  | 'upstream_unavailable'
  // The two LICENSED-MODEL refusals. A caller branches on them differently
  // from every code above: `model_not_licensed` is a licence to obtain,
  // `model_not_serving` is a fleet that has not started yet and nothing for
  // them to do. The sentences are the Python client's (`errors.py::_sentence`)
  // and reach here verbatim through `parseApiError`, so this union is the
  // TypeScript half of one wording, not a second copy of it.
  | 'model_not_licensed'
  | 'model_not_serving'
  // Not a closed set on purpose — a code added to the gateway after this was
  // written must still arrive intact rather than being coerced to a fallback.
  | (string & {});

export class CaptchaKrakenAPIError extends Error {
  readonly status: number | undefined;
  readonly code: CaptchaKrakenErrorCode | undefined;
  /** Where to send the user to fix it, when the server named somewhere. */
  readonly resolutionUrl: string | undefined;
  /** Seconds to wait, on `rate_limited`. */
  readonly retryAfterSeconds: number | undefined;
  /** Lets `catch` blocks branch without `instanceof` across module copies. */
  readonly isCaptchaKrakenAPIError = true;

  constructor(
    message: string,
    fields: {
      status?: number;
      code?: string;
      resolutionUrl?: string;
      retryAfterSeconds?: number;
    } = {},
  ) {
    super(message);
    this.name = 'CaptchaKrakenAPIError';
    this.status = fields.status;
    this.code = fields.code;
    this.resolutionUrl = fields.resolutionUrl;
    this.retryAfterSeconds = fields.retryAfterSeconds;
  }
}

/**
 * Pull a hosted-API error out of the CLI's stderr, or return null.
 *
 * Scans line by line rather than parsing the whole buffer: stderr also carries
 * timing records and any warning the Python side emitted, so the JSON payload
 * is one line among several and `JSON.parse(stderr)` would simply fail.
 *
 * Returns null for anything that is not recognisably ours, which leaves the
 * caller's existing generic handling in place — an unparseable stderr must not
 * become a confident but wrong billing message.
 */
export function parseApiError(stderr: string): CaptchaKrakenAPIError | null {
  if (!stderr || !stderr.includes('ck_error')) return null;

  for (const line of stderr.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed.startsWith('{') || !trimmed.includes('ck_error')) continue;

    let parsed: any;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      continue;
    }

    const details = parsed?.ck_error;
    if (!details || typeof details !== 'object') continue;

    const message =
      typeof parsed.error === 'string' && parsed.error.trim()
        ? parsed.error
        : 'CaptchaKraken refused this solve.';

    return new CaptchaKrakenAPIError(message, {
      status: typeof details.status === 'number' ? details.status : undefined,
      code: typeof details.code === 'string' ? details.code : undefined,
      resolutionUrl:
        typeof details.resolution_url === 'string' ? details.resolution_url : undefined,
      retryAfterSeconds:
        typeof details.retry_after_seconds === 'number'
          ? details.retry_after_seconds
          : undefined,
    });
  }

  return null;
}
