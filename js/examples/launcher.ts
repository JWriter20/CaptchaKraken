function candidates(): string[] {
  const out: string[] = [];
  const path = require('node:path');

  for (const envVar of ['HOLO_TS_PATH', 'CAMOUFOX_TS_PATH']) {
    const raw = process.env[envVar]?.trim();
    if (!raw) continue;
    const p = path.isAbsolute(raw) ? raw : path.resolve(process.cwd(), raw);
    out.push(p.endsWith('.js') ? p : path.join(p, 'dist', 'index.js'));
  }

  out.push('@jobharvest/holo', 'holo', 'camoufox', 'camoufox-js');
  return out;
}

export interface ResolvedLauncher {
  launch: (opts: any) => Promise<any>;

  from: string;
  name: 'Holo' | 'Camoufox';
}

export async function resolveLauncher(): Promise<ResolvedLauncher> {
  const { pathToFileURL } = require('node:url');
  const path = require('node:path');
  const tried: string[] = [];

  for (const spec of candidates()) {
    try {
      const url = spec.startsWith('.') || path.isAbsolute(spec)
        ? pathToFileURL(spec).href
        : spec;
      const mod: any = await import(url);
      const holo = mod.Holo ?? mod.default?.Holo;
      const camoufox = mod.Camoufox ?? mod.default?.Camoufox;
      if (holo) return { launch: holo, from: spec, name: 'Holo' };
      if (camoufox) return { launch: camoufox, from: spec, name: 'Camoufox' };
      tried.push(`${spec} (loaded, exports neither Holo nor Camoufox)`);
    } catch (e: any) {
      tried.push(`${spec} (${e?.code ?? e?.name ?? 'failed'})`);
    }
  }

  throw new Error(
    'Could not resolve a Holo or Camoufox launcher. Tried:\n' +
      tried.map((t) => `  - ${t}`).join('\n') +
      '\n\nPoint HOLO_TS_PATH at a built Holo checkout (run `npm run build` there),' +
      '\nor CAMOUFOX_TS_PATH at the typescript/ package of a camoufox checkout.',
  );
}

export function displayMode(): boolean | 'virtual' | 'virtual-gpu' {
  const v = (process.env.HEADLESS ?? 'true').trim().toLowerCase();
  if (v === 'virtual' || v === 'virtual-gpu') return v;
  if (v === '0' || v === 'false') return false;
  return true;
}

export function launchOptions(): Record<string, any> {
  const executablePath =
    process.env.HOLO_EXECUTABLE_PATH ||
    process.env.CAMOUFOX_BINARY ||
    process.env.CAMOUFOX_EXECUTABLE_PATH;
  return {
    headless: displayMode(),

    humanize: process.env.HUMANIZE === '1',
    geoip: false,
    ...(executablePath ? { executable_path: executablePath } : {}),
  };
}
