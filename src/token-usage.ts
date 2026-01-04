import { TokenUsage } from './types';

export interface ModelPricing {
  inputPricePerM: number;
  outputPricePerM: number;
  cachedInputPricePerM: number;
}

/**
 * Pricing for local models is 0 as they are self-hosted via vLLM or Transformers.
 */
export const PRICING: Record<string, ModelPricing> = {
  'default': {
    inputPricePerM: 0,
    outputPricePerM: 0,
    cachedInputPricePerM: 0,
  }
};

export function estimateCost(usage: TokenUsage): number {
  // Local models have 0 cost
  return 0;
}

export function aggregateTokenUsage(usages: TokenUsage[]) {
  if (usages.length === 0) {
    return {
      modelName: 'none',
      inputTokens: 0,
      outputTokens: 0,
      cachedInputTokens: 0,
      estimatedCost: 0,
    };
  }

  const modelName = usages[0].model;
  let inputTokens = 0;
  let outputTokens = 0;
  let cachedInputTokens = 0;
  let estimatedCost = 0;

  for (const usage of usages) {
    inputTokens += usage.input_tokens;
    outputTokens += usage.output_tokens;
    cachedInputTokens += usage.cached_input_tokens || 0;
    estimatedCost += estimateCost(usage);
  }

  return {
    modelName,
    inputTokens,
    outputTokens,
    cachedInputTokens,
    estimatedCost,
  };
}
