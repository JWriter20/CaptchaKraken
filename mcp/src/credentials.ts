/**
 * Where the token lives on disk.
 *
 * THIS FILE IS A CREDENTIAL, and it is written with that in mind: the directory
 * is 0700, the file is 0600, and it is created with those modes rather than
 * chmod'ed afterwards — a file that is briefly world-readable on a shared box is
 * a file that was world-readable.
 *
 * IT IS KEYED BY BASE URL. Someone rehearsing against dev.captchakraken.com and
 * then running against production must not have the dev token silently
 * presented to production, where it is not a token at all and every call 401s
 * with no explanation. So the store is a map from origin to what we hold for it,
 * and switching `CAPTCHAKRAKEN_BASE_URL` switches identity cleanly.
 *
 * NOTHING HERE IS ENCRYPTED. A local secret encrypted with a local key is
 * ceremony, not protection — whoever can read the file can read the key. The
 * real defences are the file mode, the token's one-year expiry, and the
 * dashboard's disconnect button.
 */

import { chmodSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

export interface StoredAccount {
  userId: number;
  githubLogin: string | null;
  email: string | null;
}

/** A sign-in that has been started but not yet approved in the browser. */
export interface PendingDevice {
  deviceCode: string;
  userCode: string;
  verificationUri: string;
  verificationUriComplete: string;
  /** Epoch millis. Past this the code is dead and `sign_in` starts a new one. */
  expiresAtMs: number;
  intervalSeconds: number;
}

export interface Credential {
  accessToken?: string;
  /** ISO 8601, as returned by the control plane. */
  expiresAt?: string;
  account?: StoredAccount;
  pending?: PendingDevice;
}

type Store = Record<string, Credential>;

function configPath(): string {
  // XDG first, because someone who has set it has done so on purpose. The
  // fallback is ~/.config on every platform rather than %APPDATA% on Windows —
  // one path is easier to tell a person to delete than three.
  const base = process.env.XDG_CONFIG_HOME?.trim() || join(homedir(), '.config');
  return join(base, 'captchakraken', 'mcp.json');
}

function readStore(): Store {
  try {
    const parsed: unknown = JSON.parse(readFileSync(configPath(), 'utf8'));
    return parsed && typeof parsed === 'object' ? (parsed as Store) : {};
  } catch {
    // Missing, empty, or corrupt all mean the same thing to every caller: we
    // hold nothing for this origin, so sign in. Refusing to start because the
    // file has a stray comma would strand someone with no obvious way out.
    return {};
  }
}

function writeStore(store: Store): void {
  const path = configPath();
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });

  /*
   * Write-then-rename, so a crash mid-write cannot leave a truncated file where
   * a valid one was — the failure that would cost someone their token for no
   * reason. The temporary file is created 0600 so the secret is never on disk
   * under a laxer mode, even for the instant before the rename.
   */
  const temporary = `${path}.${process.pid}.tmp`;
  writeFileSync(temporary, `${JSON.stringify(store, null, 2)}\n`, { mode: 0o600 });
  renameSync(temporary, path);

  // renameSync preserves the temporary file's mode, but an existing file's mode
  // wins on some filesystems. Cheap to be sure.
  chmodSync(path, 0o600);
}

export function credentialPath(): string {
  return configPath();
}

/*
 * ───────────────────────────────────────────────────────────────────────────
 * The solver credential — a SECOND, deliberately separate store.
 *
 * Two credentials exist and they must never meet:
 *
 *   ckm_…      management token. Talks to the control plane's /api/v1. Lives in
 *              mcp.json, above. Can mint and revoke keys.
 *   ck_live_…  inference key. Talks to the gateway. Lives here. Can spend money
 *              but cannot mint anything.
 *
 * Keeping them in one file would mean whatever reads the solver key at solve
 * time is also holding a token that can issue more keys — a strictly worse
 * blast radius for no gain. So this is a separate file in a separate directory,
 * and the only thing that crosses between them is that `create_api_key` writes
 * this one as a side effect.
 *
 * WHY A FILE AT ALL: `create_api_key` used to return the secret in its tool
 * result, which puts a live inference key into the agent transcript — the
 * exact thing this handoff exists to prevent. The key goes to disk at 0600 and
 * the agent is told the path.
 *
 * The Python client reads this in `config.py` (`_read_credentials_file`), which
 * accepts either name for each value; the env-file form is used because it is
 * also directly `source`-able by a human.
 * ───────────────────────────────────────────────────────────────────────────
 */

/**
 * Where the solver looks for its key.
 *
 * `CAPTCHA_KRAKEN_STATE_DIR` is honoured because the Python client honours it —
 * if the two disagreed, someone who had redirected their state dir would get a
 * file written where nothing reads it and a 401 with no explanation.
 */
export function solverCredentialPath(): string {
  const stateDir =
    process.env.CAPTCHA_KRAKEN_STATE_DIR?.trim() || join(homedir(), '.captchakraken');
  return join(stateDir, 'credentials');
}

/**
 * Write the inference key and its endpoint where the solver will find them.
 *
 * The endpoint travels WITH the key on purpose: a hosted user who has never run
 * vLLM has no reason to know what `VLLM_BASE_URL` is, and a key without an
 * endpoint authenticates flawlessly against a local port with nothing behind
 * it. Writing both means signup configures the solve path completely.
 *
 * Returns the path, which is what the caller reports in place of the secret.
 */
export function writeSolverCredential(options: {
  apiKey: string;
  baseUrl?: string;
}): string {
  const path = solverCredentialPath();

  // 0700 on the directory, created with the mode rather than chmod'ed into it:
  // a directory that is briefly world-readable is a directory that was
  // world-readable, and the file inside it is a live credential.
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });

  const lines = [
    '# CaptchaKraken inference key — written by captchakraken-mcp.',
    '# Keep this file private (0600). Revoke the key from the dashboard or the',
    '# MCP `revoke_api_key` tool if it is ever exposed.',
    `CAPTCHA_KRAKEN_API_KEY=${options.apiKey}`,
  ];
  if (options.baseUrl) lines.push(`VLLM_BASE_URL=${options.baseUrl}`);

  // Write-then-rename through a 0600 temp file, so a crash mid-write cannot
  // leave a truncated credential where a valid one was.
  const temporary = `${path}.${process.pid}.tmp`;
  writeFileSync(temporary, `${lines.join('\n')}\n`, { mode: 0o600 });
  renameSync(temporary, path);

  // renameSync preserves the temp file's mode, but an existing file's mode wins
  // on some filesystems — and this path is very likely to already exist, since
  // minting a second key overwrites the first. Cheap to be sure.
  chmodSync(path, 0o600);

  return path;
}

export function loadCredential(baseUrl: string): Credential {
  return readStore()[baseUrl] ?? {};
}

export function saveCredential(baseUrl: string, credential: Credential): void {
  const store = readStore();
  store[baseUrl] = credential;
  writeStore(store);
}

export function clearCredential(baseUrl: string): void {
  const store = readStore();
  delete store[baseUrl];
  writeStore(store);
}

/**
 * Is there a token, and is it still in date?
 *
 * The expiry is checked locally as a courtesy — the server checks it too, and
 * the server is the one that decides. This exists so `sign_in` can say "your
 * token expired" instead of the agent discovering it through a 401 on whatever
 * it was actually trying to do.
 */
export function hasLiveToken(credential: Credential): boolean {
  if (!credential.accessToken) return false;
  if (!credential.expiresAt) return true;
  const expiry = Date.parse(credential.expiresAt);
  return Number.isNaN(expiry) || expiry > Date.now();
}
