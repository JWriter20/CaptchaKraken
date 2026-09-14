import { ErrorCode } from './kinds.js';

/** Open on purpose: a code the server adds tomorrow must arrive intact, not be coerced to undefined. */
export type CaptchaKrakenErrorCode = ErrorCode | (string & {});

/**
 * The Python port words every message once and this side repeats it verbatim: two copies of the same
 * sentence drift. Before this class a customer out of credits read `vLLM 402 Payment Required at ...`.
 */
export class CaptchaKrakenAPIError extends Error {
  readonly status: number | undefined;
  readonly code: CaptchaKrakenErrorCode | undefined;

  readonly resolutionUrl: string | undefined;

  readonly retryAfterSeconds: number | undefined;

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
 * Line by line, because stderr also carries timing records. Anything unrecognised is null, never a guess:
 * an unparseable stderr must not become a confident but wrong billing message.
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
