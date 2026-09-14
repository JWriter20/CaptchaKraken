export const PRODUCTIVE = new Set(['inference', 'mouse']);

export function timingsEnabled(): boolean {
  return process.env.CAPTCHA_TIMINGS === '1';
}

export class PhaseBudget {
  readonly totals = new Map<string, number>();
  readonly counts = new Map<string, number>();
  private readonly open: string[] = [];
  private readonly t0 = Date.now();

  async phase<T>(name: string, fn: () => Promise<T>): Promise<T> {
    if (this.open.includes(name)) return fn();
    this.open.push(name);
    const t0 = Date.now();
    try {
      return await fn();
    } finally {
      this.open.splice(this.open.indexOf(name), 1);
      this.add(name, Date.now() - t0);
    }
  }

  add(name: string, ms: number): void {
    this.totals.set(name, (this.totals.get(name) ?? 0) + ms);
    this.counts.set(name, (this.counts.get(name) ?? 0) + 1);
  }

  elapsedMs(): number {
    return Date.now() - this.t0;
  }

  toObject(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const [k, v] of this.totals) out[k] = v;
    out.total = this.elapsedMs();
    return out;
  }

  report(): string {
    const total = this.elapsedMs();
    let useful = 0;
    for (const [k, v] of this.totals) if (PRODUCTIVE.has(k)) useful += v;
    const rows = [...this.totals.entries()].sort((a, b) => b[1] - a[1]);
    const lines = [
      `[BUDGET] solve ${(total / 1000).toFixed(1)}s — ` +
        `${(useful / 1000).toFixed(1)}s useful (${total ? Math.round((100 * useful) / total) : 0}%), ` +
        `${((total - useful) / 1000).toFixed(1)}s waiting`,
    ];
    for (const [name, ms] of rows) {
      const tag = PRODUCTIVE.has(name) ? '*' : ' ';
      lines.push(
        `[BUDGET] ${tag} ${name.padEnd(22)} ${(ms / 1000).toFixed(2).padStart(6)}s  x${this.counts.get(name)}`,
      );
    }
    let attributed = 0;
    for (const v of this.totals.values()) attributed += v;
    if (total - attributed > 50) {
      lines.push(`[BUDGET]   ${'(unattributed)'.padEnd(22)} ${((total - attributed) / 1000).toFixed(2).padStart(6)}s`);
    }
    return lines.join('\n');
  }
}
