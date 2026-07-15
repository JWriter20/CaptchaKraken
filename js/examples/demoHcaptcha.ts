/**
 * End-to-end demo: solve the standard hCaptcha demo page.
 *
 *   npm install                 # in js/, builds the bundled python engine
 *   npm i -D camoufox-js tsx    # example-only deps
 *   npx camoufox-js fetch        # or point CAMOUFOX_BINARY at your fork binary
 *   source ../captchakraken.env  # VLLM_BASE_URL + CAPTCHA_KRAKEN_API_KEY
 *   npx tsx examples/demoHcaptcha.ts
 *
 * Note: hCaptcha randomly serves non-grid puzzles (drag / video / choose-the-card)
 * which this solver does not handle yet — the report will say so and you can
 * re-run to get an image grid.
 */
import { runDemo } from './_harness';

runDemo({
  name: 'hCaptcha',
  url: 'https://accounts.hcaptcha.com/demo',
  vendor: 'hcaptcha',
});
