#!/usr/bin/env node
/**
 * The entry point. Stdio transport, because that is what an editor speaks.
 *
 * NOTHING MAY BE WRITTEN TO STDOUT except MCP frames. Stdout *is* the protocol
 * channel on this transport, and one stray `console.log` — a debug line, a
 * deprecation notice, a banner — corrupts the stream and the client reports the
 * server as broken rather than as chatty. Every diagnostic in this package goes
 * to stderr, which the client shows in its logs.
 */

import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';

import { createServer } from './server.js';

/**
 * Which deployment to talk to.
 *
 * Overridable so the same package can be pointed at a staging box, and so a
 * self-hosted control plane is a configuration line rather than a fork. The
 * default is production, because that is what `npx captchakraken-mcp` with no
 * arguments should mean.
 */
const BASE_URL = (process.env.CAPTCHAKRAKEN_BASE_URL ?? 'https://captchakraken.com').replace(
  /\/$/,
  '',
);

/**
 * What the approval page calls this client.
 *
 * A person approving a code should see something they recognise, and "the thing
 * I am typing in" is more recognisable than "captchakraken-mcp". Editors that
 * set this are rare, so the fallback matters more than the variable.
 */
const CLIENT_NAME =
  process.env.CAPTCHAKRAKEN_CLIENT_NAME?.trim() || 'MCP client';

async function main(): Promise<void> {
  const server = createServer(BASE_URL, CLIENT_NAME);
  await server.connect(new StdioServerTransport());
  console.error(`[captchakraken-mcp] connected, talking to ${BASE_URL}`);
}

main().catch((error: unknown) => {
  console.error('[captchakraken-mcp] fatal:', error instanceof Error ? error.stack : error);
  process.exit(1);
});
