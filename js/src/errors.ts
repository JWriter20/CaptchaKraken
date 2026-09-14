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

  | 'model_not_licensed'
  | 'model_not_serving'

  | (string & {});

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
