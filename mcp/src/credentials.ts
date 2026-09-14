/**
 * Two credentials in two files on purpose: the management token (`ckm_`) can mint keys, the solver key
 * (`ck_live_`) can only spend, and one file holding both is a strictly worse blast radius for no gain.
 * Nothing is encrypted by choice: an unlockable-by-the-same-user secret is ceremony, not protection.
 * See TRIBAL_KNOWLEDGE.md.
 */
import { chmodSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

export interface StoredAccount {
  userId: number;
  githubLogin: string | null;
  email: string | null;
}

export interface PendingDevice {
  deviceCode: string;
  userCode: string;
  verificationUri: string;
  verificationUriComplete: string;

  expiresAtMs: number;
  intervalSeconds: number;
}

export interface Credential {
  accessToken?: string;

  expiresAt?: string;
  account?: StoredAccount;
  pending?: PendingDevice;
}

/** Keyed by base URL so a dev token is never silently presented to production. */
type Store = Record<string, Credential>;

// XDG then ~/.config on every platform, not %APPDATA%: one path is easier to tell a person to delete than three.
function configPath(): string {
  const base = process.env.XDG_CONFIG_HOME?.trim() || join(homedir(), '.config');
  return join(base, 'captchakraken', 'mcp.json');
}

function readStore(): Store {
  try {
    const parsed: unknown = JSON.parse(readFileSync(configPath(), 'utf8'));
    return parsed && typeof parsed === 'object' ? (parsed as Store) : {};
  } catch {
    return {};
  }
}

function writeStore(store: Store): void {
  const path = configPath();
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });

  // Write-then-rename from a 0600 temp, plus a trailing chmod: an existing file's mode wins on some filesystems.
  const temporary = `${path}.${process.pid}.tmp`;
  writeFileSync(temporary, `${JSON.stringify(store, null, 2)}\n`, { mode: 0o600 });
  renameSync(temporary, path);

  chmodSync(path, 0o600);
}

export function credentialPath(): string {
  return configPath();
}

/** Honours CAPTCHA_KRAKEN_STATE_DIR because the Python client does; the two must agree on where the key lives. */
export function solverCredentialPath(): string {
  const stateDir =
    process.env.CAPTCHA_KRAKEN_STATE_DIR?.trim() || join(homedir(), '.captchakraken');
  return join(stateDir, 'credentials');
}

export function writeSolverCredential(options: {
  apiKey: string;
  baseUrl?: string;
}): string {
  const path = solverCredentialPath();

  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });

  const lines = [
    '# CaptchaKraken inference key — written by captchakraken-mcp.',
    '# Keep this file private (0600). Revoke the key from the dashboard or the',
    '# MCP `revoke_api_key` tool if it is ever exposed.',
    `CAPTCHA_KRAKEN_API_KEY=${options.apiKey}`,
  ];
  // The endpoint travels with the key: a key without one authenticates flawlessly against a local port with nothing behind it.
  if (options.baseUrl) lines.push(`VLLM_BASE_URL=${options.baseUrl}`);

  const temporary = `${path}.${process.pid}.tmp`;
  writeFileSync(temporary, `${lines.join('\n')}\n`, { mode: 0o600 });
  renameSync(temporary, path);

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

export function hasLiveToken(credential: Credential): boolean {
  if (!credential.accessToken) return false;
  if (!credential.expiresAt) return true;
  const expiry = Date.parse(credential.expiresAt);
  return Number.isNaN(expiry) || expiry > Date.now();
}
