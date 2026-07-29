/**
 * Numbers that must agree with the hosted gateway, stated once.
 *
 * These are not arbitrary defaults. They sit inside an ordering the server
 * enforces, and the ordering — not any individual value — is the property worth
 * protecting. `limits.test.ts` pins it.
 */

/**
 * The server bills at most this many responses per captcha attempt.
 *
 * Mirrors `MAX_BILLABLE_ROUNDS_PER_SESSION` in captchakraken-gateway's
 * `src/pricing.ts`. Declared here so the ordering below can be asserted; the
 * gateway is what actually enforces it.
 */
export const SERVER_MAX_BILLABLE_ROUNDS = 5;

/**
 * The server answers at most this many responses per attempt, then refuses the
 * eleventh with 409 `solve_abandoned`.
 *
 * Mirrors `MAX_ROUNDS_PER_SESSION` in the gateway.
 */
export const SERVER_MAX_SERVED_ROUNDS = 10;

/**
 * How many click → refresh → re-solve rounds the client will attempt on one
 * reCAPTCHA 3x3 dynamic puzzle.
 *
 * ── WHY 8, AND WHY IT MUST STAY STRICTLY BETWEEN 5 AND 10 ───────────────────
 *
 * The three numbers describe one attempt in three stages:
 *
 *     rounds 1–5     billed
 *     rounds 6–10    served, and comped
 *     round 11       refused — 409, the attempt is over
 *
 * 8 sits strictly inside that, with room on both sides, and both margins do a
 * job:
 *
 *   Above 5 — rounds 6, 7 and 8 are free. A puzzle that needs a couple of extra
 *   passes still gets them, and the customer is not charged for our model
 *   needing another look. Dropping to 5 would abandon winnable puzzles at
 *   exactly the point where continuing costs the customer nothing.
 *
 *   Below 10 — a well-behaved client gives up BEFORE the server refuses it.
 *   Raising this to 10 or beyond means the normal end of a hard puzzle is a
 *   409 rather than a clean local stop, which turns an ordinary outcome into an
 *   error the caller has to interpret.
 *
 * So: if the billable cap moves, re-derive this and move the test in the same
 * commit. Changing one number alone silently breaks one of the two margins.
 */
export const DEFAULT_RECAPTCHA_MAX_DYNAMIC_ROUNDS = 8;
