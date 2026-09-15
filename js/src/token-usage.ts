import { TokenUsage } from './types';

// The CLI emits the vLLM/OpenAI shape (`prompt_tokens`); older rows used `input_tokens`.
const inTok = (u: TokenUsage) => (u.input_tokens ?? (u as any).prompt_tokens ?? 0) as number;
const outTok = (u: TokenUsage) => (u.output_tokens ?? (u as any).completion_tokens ?? 0) as number;
const cachedTok = (u: TokenUsage) => (u.cached_input_tokens ?? (u as any).prompt_tokens_details?.cached_tokens ?? 0) as number;

/** One summed row for a solve. The client has no price list, so `estimatedCost` is always 0. */
export function aggregateTokenUsage(usages: TokenUsage[]) {
  return {
    modelName: usages[0]?.model ?? 'none',
    inputTokens: usages.reduce((n, u) => n + inTok(u), 0),
    outputTokens: usages.reduce((n, u) => n + outTok(u), 0),
    cachedInputTokens: usages.reduce((n, u) => n + cachedTok(u), 0),
    estimatedCost: 0,
  };
}
