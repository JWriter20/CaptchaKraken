import { loadCredential, saveCredential } from './credentials.js';

const TIMEOUT_MS = 20_000;

/** The one code this client mints itself; every other code is the control plane's. */
export const NOT_SIGNED_IN = 'not_signed_in';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

export interface ApiOptions {
  method?: 'GET' | 'POST';
  body?: unknown;

  authenticated?: boolean;
}

export class ControlPlane {
  readonly baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  async request<T>(path: string, options: ApiOptions = {}): Promise<T> {
    const { method = 'GET', body, authenticated = true } = options;

    const headers: Record<string, string> = {
      accept: 'application/json',

      'user-agent': 'captchakraken-mcp',
    };
    if (body !== undefined) headers['content-type'] = 'application/json';

    if (authenticated) {
      const credential = loadCredential(this.baseUrl);
      if (!credential.accessToken) {
        throw new ApiError(401, NOT_SIGNED_IN, 'Not signed in. Run the sign_in tool first.');
      }
      headers.authorization = `Bearer ${credential.accessToken}`;
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: controller.signal,
      });
    } catch (error) {
      throw new ApiError(
        0,
        'network_error',
        `Could not reach ${this.baseUrl}: ${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      clearTimeout(timer);
    }

    const payload = await readJson(response);

    if (!response.ok) {
      const code = typeof payload?.error === 'string' ? payload.error : `http_${response.status}`;
      const message =
        typeof payload?.message === 'string'
          ? payload.message
          : `${this.baseUrl}${path} returned ${response.status}`;

      // A 401 on an authenticated call drops the stored token so the next sign_in starts clean. Not on the
      // device endpoints: they answer 400/401 for reasons that have nothing to do with a stored token.
      if (response.status === 401 && authenticated) {
        const credential = loadCredential(this.baseUrl);
        delete credential.accessToken;
        delete credential.expiresAt;
        saveCredential(this.baseUrl, credential);
      }

      throw new ApiError(response.status, code, message);
    }

    return payload as T;
  }
}

async function readJson(response: Response): Promise<Record<string, unknown> | null> {
  try {
    const parsed: unknown = await response.json();
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
