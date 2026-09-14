import { spawn } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import assert from 'node:assert/strict';

const CONTRACT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'contract.json');

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

  const declared = JSON.parse(readFileSync('package.json', 'utf8')).version;
  const advertised = init.result?.serverInfo?.version;
  if (advertised !== declared) {
    throw new Error(
      `serverInfo.version is ${advertised} but package.json says ${declared} — ` +
        'the version an MCP client sees must be the version that shipped',
    );
  }

  send({ jsonrpc: '2.0', method: 'notifications/initialized' });

  const tools = await request(2, 'tools/list', {});
  const names = (tools.result?.tools ?? []).map((t) => t.name);
  console.log(`tools/list OK — ${names.length} tools:`);
  for (const n of names) console.log('  -', n);

  const live = Object.fromEntries(
    (tools.result?.tools ?? [])
      .map((t) => [
        t.name,
        {
          properties: Object.keys(t.inputSchema?.properties ?? {}).sort(),
          required: (t.inputSchema?.required ?? []).slice().sort(),
        },
      ])
      .sort((a, b) => a[0].localeCompare(b[0])),
  );

  const contract = JSON.parse(readFileSync(CONTRACT, 'utf8'));
  if (process.env.CONTRACT_WRITE === '1') {
    contract.mcp = { server_name: init.result?.serverInfo?.name, tools: live };
    writeFileSync(CONTRACT, JSON.stringify(contract, null, 2) + '\n');
    console.log(`wrote ${CONTRACT}`);
  }

  assert.deepEqual(
    live,
    contract.mcp.tools,
    'the MCP tool surface changed. An agent calls these by name and fills them ' +
      'by field, so a rename or a dropped parameter breaks every configured ' +
      'client silently. If intended, re-run with CONTRACT_WRITE=1 in the same ' +
      'commit and bump mcp/package.json.',
  );
  assert.equal(init.result?.serverInfo?.name, contract.mcp.server_name);
  console.log(`contract OK — ${Object.keys(live).length} tools match contract.json`);
  process.exitCode = 0;
} catch (e) {
  console.error('FAILED:', e.message);
  console.error('stderr was:\n' + stderrChunks.join(''));
  process.exitCode = 1;
} finally {
  proc.kill();
}
