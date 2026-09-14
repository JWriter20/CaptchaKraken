#!/usr/bin/env node

import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';

import { createServer } from './server.js';

const BASE_URL = (process.env.CAPTCHAKRAKEN_BASE_URL ?? 'https://captchakraken.com').replace(
  /\/$/,
  '',
);

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
