// The gateway's three tiers: rounds 1-5 are billed, 6-10 are served and comped, and the 11th is refused with 409.
export const SERVER_MAX_BILLABLE_ROUNDS = 5;

export const SERVER_MAX_SERVED_ROUNDS = 10;

// Inside the comped band with margin on both sides. If the billable cap moves, re-derive this and move limits.test.ts in the same commit.
export const DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS = 8;
