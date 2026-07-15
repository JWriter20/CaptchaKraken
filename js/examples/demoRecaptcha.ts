/**
 * End-to-end demo: solve Google's standard reCAPTCHA v2 demo page.
 *
 *   npm install                 # in js/, builds the bundled python engine
 *   npm i -D camoufox-js tsx    # example-only deps
 *   npx camoufox-js fetch        # or point CAMOUFOX_BINARY at your fork binary
 *   source ../captchakraken.env  # VLLM_BASE_URL + CAPTCHA_KRAKEN_API_KEY
 *   npx tsx examples/demoRecaptcha.ts
 */
import { runDemo } from './_harness';

runDemo({
  name: 'reCAPTCHA v2',
  url: 'https://www.google.com/recaptcha/api2/demo',
  vendor: 'recaptcha',
});
