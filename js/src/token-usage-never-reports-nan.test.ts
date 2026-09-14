import { test } from 'node:test';
import assert from 'node:assert/strict';

import { aggregateTokenUsage } from './token-usage';
import { TokenUsage } from './types';

const MODEL = 'captcha-v12';

function vllmRound(prompt: number, completion: number): TokenUsage {
  return { model: MODEL, prompt_tokens: prompt, completion_tokens: completion } as any;
}

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
  const totals = aggregateTokenUsage([]);

  assert.equal(totals.inputTokens, 0);
  assert.equal(totals.outputTokens, 0);
  assert.equal(totals.cachedInputTokens, 0);
  assert.equal(totals.estimatedCost, 0);
  assertAllFinite(totals);
});

test('the CLI dialect is counted, not dropped to zero', () => {
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
  const totals = aggregateTokenUsage([vllmRound(100, 10), { model: MODEL } as any]);

  assert.equal(totals.inputTokens, 100);
  assert.equal(totals.outputTokens, 10);
  assertAllFinite(totals);
});

test('cached tokens are read from the nested OpenAI field', () => {
  const totals = aggregateTokenUsage([
    { model: MODEL, prompt_tokens: 800, completion_tokens: 12,
      prompt_tokens_details: { cached_tokens: 640 } } as any,
  ]);

  assert.equal(totals.cachedInputTokens, 640);
});

test('an unknown model still produces a finite cost instead of NaN', () => {
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

