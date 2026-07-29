/**
 * A twenty-line MCP client, so CI can prove the server actually speaks MCP.
 *
 *   node .github/scripts/mcp-smoke.mjs node dist/index.js
 *
 * Does the real handshake over stdio — initialize, notifications/initialized,
 * tools/list — and exits non-zero unless at least one tool comes back.
 *
 * WHY THIS EXISTS RATHER THAN JUST `tsc`: the failure mode worth catching is a
 * server that compiles perfectly and then cannot complete a handshake, because
 * that ships green and breaks in the user's editor. The two ways it happens are
 * a transport wired up wrong and — much more likely here — something writing to
 * stdout, which IS the protocol channel: one stray console.log corrupts every
 * frame. This catches both.
 *
 * Hermetic: tools/list is answered before any sign-in, so no credentials, no
 * network, and nothing to clean up.
 */
import { spawn } from 'node:child_process';

const cmd = process.argv[2];
const args = process.argv.slice(3);
const proc = spawn(cmd, args, { stdio: ['pipe', 'pipe', 'pipe'] });

let buf = '';
const pending = new Map();

proc.stdout.on('data', (d) => {
  buf += d.toString();
  let i;
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i).trim();
    buf = buf.slice(i + 1);
    if (!line) continue;
    let msg;
    try { msg = JSON.parse(line); } catch { console.error('non-JSON on stdout:', line); continue; }
    if (msg.id && pending.has(msg.id)) pending.get(msg.id)(msg);
  }
});

const stderrChunks = [];
proc.stderr.on('data', (d) => stderrChunks.push(d.toString()));

function send(obj) { proc.stdin.write(JSON.stringify(obj) + '\n'); }
function request(id, method, params) {
  return new Promise((resolve, reject) => {
    pending.set(id, resolve);
    send({ jsonrpc: '2.0', id, method, params });
    setTimeout(() => reject(new Error(`timeout waiting for ${method}`)), 15000);
  });
}

try {
  const init = await request(1, 'initialize', {
    protocolVersion: '2024-11-05',
    capabilities: {},
    clientInfo: { name: 'smoke', version: '0.0.0' },
  });
  console.log('initialize OK — server:', JSON.stringify(init.result?.serverInfo));

  send({ jsonrpc: '2.0', method: 'notifications/initialized' });

  const tools = await request(2, 'tools/list', {});
  const names = (tools.result?.tools ?? []).map((t) => t.name);
  console.log(`tools/list OK — ${names.length} tools:`);
  for (const n of names) console.log('  -', n);
  process.exitCode = names.length > 0 ? 0 : 1;
} catch (e) {
  console.error('FAILED:', e.message);
  console.error('stderr was:\n' + stderrChunks.join(''));
  process.exitCode = 1;
} finally {
  proc.kill();
}
