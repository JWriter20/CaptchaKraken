/**
 * Token accounting is reported to the caller, so it must never be `NaN`.
 *
 * `SolveResult.tokenUsage` is part of the published surface: callers log it,
 * bill against it, and put it on dashboards. It is summed from whatever the
 * inference endpoint returned for each round of a solve, and those rounds do not
 * all speak the same dialect — the CLI emits the OpenAI/vLLM shape
 * (`prompt_tokens` / `completion_tokens`) while older records carry
 * `input_tokens` / `output_tokens`.
 *
 * A shape that is not recognised does not throw. It contributes `undefined`,
 * the sum becomes `NaN`, and `NaN` serialises to `null` in JSON — so the round
 * is silently missing from a total that still looks like a total.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { aggregateTokenUsage } from './token-usage';
import { TokenUsage } from './types';

const MODEL = 'captcha-v12';

/** The shape the shipped CLI actually returns. */
function vllmRound(prompt: number, completion: number): TokenUsage {
  return { model: MODEL, prompt_tokens: prompt, completion_tokens: completion } as any;
}

/** The older shape, still present in stored records. */
function legacyRound(input: number, output: number): TokenUsage {
  return { model: MODEL, input_tokens: input, output_tokens: output };
}

function assertAllFinite(totals: Record<string, unknown>) {
  for (const [key, value] of Object.entries(totals)) {
    if (typeof value === 'number') {
      assert.ok(Number.isFinite(value), `${key} is not a finite number: ${value}`);
    }
  }
}

test('a solve that used no tokens reports zeros, not NaN and not an empty object', () => {
  // Reached whenever a captcha is solved without asking the model — a checkbox
  // that just needed clicking. The caller still reads the fields.
  const totals = aggregateTokenUsage([]);

  assert.equal(totals.inputTokens, 0);
  assert.equal(totals.outputTokens, 0);
  assert.equal(totals.cachedInputTokens, 0);
  assert.equal(totals.estimatedCost, 0);
  assertAllFinite(totals);
});

test('the CLI dialect is counted, not dropped to zero', () => {
  // This is the shape every real solve produces today. If it were unrecognised
  // the totals would be a confident zero on a solve that cost real tokens.
  const totals = aggregateTokenUsage([vllmRound(1200, 40)]);

  assert.equal(totals.inputTokens, 1200);
  assert.equal(totals.outputTokens, 40);
  assertAllFinite(totals);
});

test('the legacy dialect is counted too', () => {
  const totals = aggregateTokenUsage([legacyRound(900, 30)]);

  assert.equal(totals.inputTokens, 900);
  assert.equal(totals.outputTokens, 30);
});

test('rounds in different dialects add up across one solve', () => {
  // A reCAPTCHA 3x3 that refreshes tiles takes several rounds, and a stored
  // record can be replayed beside a live one.
  const totals = aggregateTokenUsage([
    vllmRound(1000, 20),
    legacyRound(500, 10),
    vllmRound(250, 5),
  ]);

  assert.equal(totals.inputTokens, 1750);
  assert.equal(totals.outputTokens, 35);
  assertAllFinite(totals);
});

test('a round with no token fields at all contributes zero rather than NaN', () => {
  // An endpoint that omits `usage` entirely, or a truncated record. One such
  // round used to poison the whole total.
  const totals = aggregateTokenUsage([vllmRound(100, 10), { model: MODEL } as any]);

  assert.equal(totals.inputTokens, 100);
  assert.equal(totals.outputTokens, 10);
  assertAllFinite(totals);
});

test('cached tokens are read from the nested OpenAI field', () => {
  // vLLM reports prefix-cache hits as `prompt_tokens_details.cached_tokens`.
  // Missing them understates reuse on exactly the boards that repeat most.
  const totals = aggregateTokenUsage([
    { model: MODEL, prompt_tokens: 800, completion_tokens: 12,
      prompt_tokens_details: { cached_tokens: 640 } } as any,
  ]);

  assert.equal(totals.cachedInputTokens, 640);
});

test('an unknown model still produces a finite cost instead of NaN', () => {
  // The model name is configuration, and a caller can serve any adapter name.
  // An unpriced name must not turn the cost field into NaN for the whole solve.
  const totals = aggregateTokenUsage([
    { model: 'some-adapter-nobody-priced', prompt_tokens: 1000, completion_tokens: 20 } as any,
  ]);

  assert.ok(Number.isFinite(totals.estimatedCost));
  assert.ok(totals.estimatedCost >= 0);
});

test('the model of the solve is reported, so a total can be attributed', () => {
  const totals = aggregateTokenUsage([vllmRound(10, 1), vllmRound(20, 2)]);
  assert.equal(totals.modelName, MODEL);
});

test('cost grows with usage rather than staying flat', () => {
  // Weak on purpose: the rate card that bills a customer is the hosted one, per
  // response, not this per-token estimate. What must hold is that the estimate
  // responds to the tokens it is given — a constant would be worse than absent.
  const small = aggregateTokenUsage([vllmRound(1000, 10)]);
  const large = aggregateTokenUsage([vllmRound(100_000, 1000)]);

  assert.ok(large.estimatedCost > small.estimatedCost);
  assertAllFinite(small);
  assertAllFinite(large);
});
