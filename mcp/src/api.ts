/**
 * The HTTP client for `/api/v1` on the control plane.
 *
 * ONE PLACE THAT TALKS TO THE NETWORK, so the timeout, the error shape and the
 * "your token stopped working" case are decided once. Every tool in server.ts
 * goes through here and none of them calls `fetch`.
 *
 * ERRORS ARE THROWN AS `ApiError` WITH THE SERVER'S CODE INTACT. The control
 * plane deliberately returns a stable `error` string and a human `message`; the
 * message is what an agent shows a person, and the code is what the tools
 * branch on. Flattening them into one string would mean parsing English to
 * decide whether to prompt for a sign-in.
 */

import { loadCredential, saveCredential } from './credentials.js';

/** Every request gets this long. A hung control plane must not hang the editor. */
const TIMEOUT_MS = 20_000;

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
  /** Send the stored token. False for the device endpoints, which mint one. */
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
      // Named so a support conversation can tell an MCP client apart from curl
      // in an access log. It carries no authority.
      'user-agent': 'captchakraken-mcp',
    };
    if (body !== undefined) headers['content-type'] = 'application/json';

    if (authenticated) {
      const credential = loadCredential(this.baseUrl);
      if (!credential.accessToken) {
        throw new ApiError(401, 'not_signed_in', 'Not signed in. Run the sign_in tool first.');
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
      // A DNS failure, a refused connection and our own abort all land here, and
      // they mean the same thing to the person reading it: we could not reach
      // the service. The cause is included because "which one" is the first
      // thing anyone debugging asks.
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

      /*
       * A 401 on an authenticated call means the token we hold is dead —
       * revoked from the dashboard, expired, or from a deployment this is no
       * longer pointed at. Dropping it here is what turns the next `sign_in`
       * into a clean new flow instead of a confusing "already signed in".
       *
       * Only on authenticated calls: the device endpoints answer 400/401 for
       * reasons that have nothing to do with a stored token.
       */
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
    // An HTML error page from a proxy in front of the app, most likely. The
    // caller turns this into "returned <status>", which is more use than a JSON
    // parse error nobody can act on.
    return null;
  }
}
