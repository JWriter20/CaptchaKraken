import { test } from '@playwright/test';
import * as path from 'path';
import * as dotenv from 'dotenv';
import { Camoufox } from '@jobharvest/camoufox-js';

dotenv.config();
dotenv.config({ path: path.resolve(__dirname, '..', '..', '.env') });

const HCAPTCHA_URL = process.env.HCAPTCHA_DEMO_URL ?? 'https://accounts.hcaptcha.com/demo';

test('probe hcaptcha challenge DOM', async () => {
  test.slow();
  const headless: boolean | 'virtual' =
    process.platform === 'linux' ? 'virtual' :
    process.env.CAPTCHA_HEADED === '1' ? false : true;
  const browser = await Camoufox({ headless } as any);
  const context = await browser.newContext();
  const page = await context.newPage();

  await page.goto(HCAPTCHA_URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2500);

  // Click the "I am human" checkbox to open the challenge.
  const checkboxIframe = await page.$('iframe[src*="hcaptcha"][src*="frame=checkbox"]');
  const cbFrame = await checkboxIframe!.contentFrame();
  await cbFrame!.click('#checkbox');

  // Wait for the challenge frame to appear, then probe it repeatedly so we can
  // see the DOM both before and after the tiles paint.
  for (let i = 0; i < 12; i++) {
    await page.waitForTimeout(700);
    const chIframe = await page.$('iframe[src*="hcaptcha"][src*="frame=challenge"]');
    if (!chIframe || !(await chIframe.isVisible())) {
      console.log(`[t=${i}] no visible challenge frame yet`);
      continue;
    }
    const frame = await chIframe.contentFrame();
    if (!frame) { console.log(`[t=${i}] no content frame`); continue; }

    const canvasInfo = await frame.evaluate(() => {
      const c = document.querySelector('canvas') as HTMLCanvasElement | null;
      if (!c) return null;
      const rect = c.getBoundingClientRect();
      let nonBlank = false;
      let distinctColors = 0;
      try {
        const ctx = c.getContext('2d');
        if (ctx) {
          const { data } = ctx.getImageData(0, 0, c.width, c.height);
          const seen = new Set<number>();
          for (let i = 0; i < data.length; i += 4 * 200) {
            const key = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
            seen.add(key);
            if (data[i + 3] > 0 && (data[i] || data[i + 1] || data[i + 2])) nonBlank = true;
          }
          distinctColors = seen.size;
        }
      } catch (e) {
        return { w: c.width, h: c.height, rectW: rect.width, rectH: rect.height, tainted: true };
      }
      return { w: c.width, h: c.height, rectW: rect.width, rectH: rect.height, nonBlank, distinctColors };
    });
    console.log(`[t=${i}] canvas=` + JSON.stringify(canvasInfo));

    const info = await frame.evaluate(() => {
      const q = (sel: string) => document.querySelectorAll(sel).length;
      const sample = (sel: string) => {
        const el = document.querySelector(sel) as HTMLElement | null;
        if (!el) return null;
        return {
          bg: getComputedStyle(el).backgroundImage.slice(0, 60),
          html: el.outerHTML.slice(0, 120),
        };
      };
      const promptEl = document.querySelector(
        '.prompt-text, h2.prompt-text, .challenge-prompt, [class*="prompt"]',
      ) as HTMLElement | null;
      // List the distinct class names present, to discover the real selectors.
      const classes = new Set<string>();
      document.querySelectorAll('*').forEach((el) => {
        el.classList.forEach((c) => classes.add(c));
      });
      return {
        bodyClass: document.body.className,
        counts: {
          'task-image': q('.task-image'),
          'task .image': q('.task .image'),
          '.image': q('.image'),
          canvas: q('canvas'),
          imgTags: q('img'),
        },
        promptText: promptEl?.textContent?.trim()?.slice(0, 80) ?? null,
        promptClass: promptEl?.className ?? null,
        firstImageSample: sample('.image') || sample('.task-image') || sample('[style*="background"]'),
        imageyClasses: [...classes].filter((c) =>
          /task|image|tile|prompt|challenge|grid|example/i.test(c),
        ),
      };
    });
    console.log(`[t=${i}] ` + JSON.stringify(info, null, 2));
  }

  await context.close();
  await browser.close();
});
