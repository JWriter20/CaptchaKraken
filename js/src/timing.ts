import { Phase } from './kinds.js';

// Always collected, printed only under CAPTCHA_TIMINGS=1: a budget you have to opt into is one nobody has when the slow solve happens.
export const PRODUCTIVE: ReadonlySet<Phase> = new Set<Phase>([Phase.INFERENCE, Phase.MOUSE]);

export function timingsEnabled(): boolean {
  return process.env.CAPTCHA_TIMINGS === '1';
}

/** Phases attribute, they do not partition: a nested phase counts under both names, and re-entering an open one counts once. */
export class PhaseBudget {
  readonly totals = new Map<Phase, number>();
  readonly counts = new Map<Phase, number>();
  private readonly open: Phase[] = [];
  private readonly t0 = Date.now();

  async phase<T>(name: Phase, fn: () => Promise<T>): Promise<T> {
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

  add(name: Phase, ms: number): void {
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
