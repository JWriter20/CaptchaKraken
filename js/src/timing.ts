/**
 * Where one solve's wall-clock went, by phase.
 *
 * The TypeScript half of `python/src/captchakraken/timing.py`; the two are one
 * implementation in two languages and `test_timing_parity.py` pins that they
 * name the same phases. That parity is the point: without it the ports can only
 * be compared on their totals, and "the JS port is four seconds slower" is not
 * a bug report — "the JS port spends four seconds starting a Python
 * interpreter it did not need to start" is.
 *
 * Always on. It is a few map updates per phase against multi-second waits, and
 * a budget you have to opt into is one nobody has when the slow solve happens.
 * Only PRINTED under `CAPTCHA_TIMINGS=1`; always RETURNED on `SolveResult`.
 */

/**
 * What a phase's time COUNTS AS, when asking how much of a solve was useful.
 *
 * Only two kinds of second are worth spending: one the model is thinking in,
 * and one the pointer is travelling in (which has to look human, so it cannot
 * be rushed). Everything else is the driver waiting on a clock, and every such
 * wait is a candidate for deletion or for overlapping with something real.
 */
export const PRODUCTIVE = new Set(['inference', 'mouse']);

export function timingsEnabled(): boolean {
  return process.env.CAPTCHA_TIMINGS === '1';
}

export class PhaseBudget {
  readonly totals = new Map<string, number>();
  readonly counts = new Map<string, number>();
  private readonly open: string[] = [];
  private readonly t0 = Date.now();

  /**
   * Attribute `fn`'s wall-clock to `name`.
   *
   * Phases may nest (a burst contains its screenshots). Only the OUTERMOST of a
   * given NAME accumulates, so re-entering one cannot double-count it — but a
   * phase nested inside a differently-named one counts under both, deliberately:
   * the cursor drifting over the widget WHILE the model generates is genuinely
   * both `mouse` and `inference`, and hiding either would misreport what the
   * solve was doing. The totals are therefore an attribution, not a partition,
   * and can exceed the elapsed time.
   */
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

  /** Record a measured span directly, for blocks that would need re-indenting. */
  add(name: string, ms: number): void {
    this.totals.set(name, (this.totals.get(name) ?? 0) + ms);
    this.counts.set(name, (this.counts.get(name) ?? 0) + 1);
  }

  elapsedMs(): number {
    return Date.now() - this.t0;
  }

  /** Plain object for `SolveResult.phases`, with the solve total folded in. */
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
