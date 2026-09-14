/**
 * `videoBurstDurationMs` is a WALL-CLOCK budget, and a slow camera cannot spend it.
 *
 * The burst decides "is this board moving on its own" by watching for a
 * floor-length window — long enough to outlast the longest dwell a real cycle
 * holds a screen for. That is a claim about SECONDS. Counting frames says the
 * same thing only while the loop actually reaches `videoBurstFps`, and a loop
 * whose camera is slower than its interval never sleeps, so the window
 * stretches by exactly however slow the camera is.
 *
 * Measured on one element screenshot, same fixture, same box:
 *
 *   the desktop browser                   15.8 ms  -> the loop paces at 10fps
 *   chromium at a phone's DPR (2.625)    183.5 ms  -> the loop free-runs at 5.4fps
 *
 * so the same 40-frame window meant 4.0s on one arm and 8.3s on the other,
 * while the answer it was waiting on had been finished for six of them.
 *
 * The numbers here are scaled down from the shipped defaults so the test costs
 * under a second; what is under test is the RULE, and it is the ratio between
 * the camera and the interval that expresses the bug.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { resolve } from 'node:path';

import { CaptchaKrakenSolver } from './solver';

const FLOOR_MS = 500;
const CEILING_MS = 1500;
const FRAME_COST_MS = 200;      // 2x the 100ms interval, as the phone arm is

/** An element whose screenshot is slower than the burst's interval. */
function slowCamera(screens: string[]) {
  let n = 0;
  return {
    shots: () => n,
    element: {
      screenshot: async ({ path }: { path: string }) => {
        const body = screens[n % screens.length];
        n += 1;
        await new Promise((r) => setTimeout(r, FRAME_COST_MS));
        fs.writeFileSync(path, body);
      },
    } as any,
  };
}

function solver() {
  return new CaptchaKrakenSolver({
    videoBurstDurationMs: FLOOR_MS,
    videoBurstMaxMs: CEILING_MS,
    videoBurstFps: 10,
  }) as any;
}

test('a still board does not hold the burst longer than the floor', async () => {
  const cam = slowCamera(['one-screen']);
  const s = solver();
  const t0 = Date.now();
  const rec = s.startKeyframeBurst(cam.element);
  const dir = await rec.finish();
  const elapsed = Date.now() - t0;
  fs.rmSync(dir, { recursive: true, force: true });

  // The window closes one floor after the LAST NEW SCREEN, and on a still
  // board that screen is the first frame — so the earliest sound exit is a
  // floor plus the frame that found it, and the frame that observes the
  // closure lands one frame later again. Anything past that is the frame count
  // spending the budget: at this camera speed the old rule took 1200ms.
  assert.ok(elapsed <= FLOOR_MS + FRAME_COST_MS * 2,
    `a still board held the burst for ${elapsed}ms against a ${FLOOR_MS}ms floor `
    + `(${cam.shots()} frames). The window is counted in frames, so a camera `
    + `slower than the interval spends the budget it was given`);
});

test('the floor is still a floor when the camera is fast', async () => {
  // A burst cut below `videoBurstDurationMs` never outlasts a cycle's dwell,
  // which is the failure the window exists to prevent.
  let n = 0;
  const element: any = {
    screenshot: async ({ path }: { path: string }) => {
      n += 1;
      fs.writeFileSync(path, 'one-screen');
    },
  };
  const s = solver();
  const t0 = Date.now();
  const rec = s.startKeyframeBurst(element);
  const dir = await rec.finish();
  const elapsed = Date.now() - t0;
  fs.rmSync(dir, { recursive: true, force: true });

  assert.ok(elapsed >= FLOOR_MS,
    `a still board was filmed for only ${elapsed}ms against a ${FLOOR_MS}ms floor (${n} frames)`);
});

test('a board that never settles stops at the ceiling in seconds', async () => {
  // Three screens that never repeat: the cycle never closes and nothing ever
  // settles, so the only exit is the ceiling. This is the shape that reached
  // 31s on the phone arm inside a 45s solve budget.
  const cam = slowCamera(['a', 'b', 'c']);
  const s = solver();
  const t0 = Date.now();
  const rec = s.startKeyframeBurst(cam.element);
  const dir = await rec.finish();
  const elapsed = Date.now() - t0;
  fs.rmSync(dir, { recursive: true, force: true });

  assert.ok(elapsed <= CEILING_MS * 1.4,
    `a board that never settles filmed for ${elapsed}ms against a ${CEILING_MS}ms `
    + `ceiling (${cam.shots()} frames)`);
});

test('the recording reports the rate it achieved, not the one it aimed at', async () => {
  // The slicer dates frames by this rate, and "40 frames at 10fps" for a burst
  // that ran at 5.4 is what made the stretched window invisible in the logs.
  const cam = slowCamera(['one-screen']);
  const s = solver();
  const rec = s.startKeyframeBurst(cam.element);
  const dir = await rec.finish();
  fs.rmSync(dir, { recursive: true, force: true });

  assert.ok(s.lastBurstFps < 10,
    `the burst reported ${s.lastBurstFps}fps from a camera costing ${FRAME_COST_MS}ms a frame`);
});

test('the python port counts the same window in milliseconds', () => {
  // CLAUDE.md 1c. Tier 3 drives both ports through one fixture, so a window
  // that means seconds in one port and frames in the other does not read as a
  // bug — it reads as the two ports disagreeing about how long a board takes.
  // `__dirname` is src/ under tsx and .test-build/ when compiled; both are one
  // level under js/, which is the idiom contract.test.ts already uses.
  const py = fs.readFileSync(resolve(
    __dirname, '..', '..', 'python', 'src', 'captchakraken', 'page_solver.py'), 'utf8');
  assert.ok(py.includes('elapsed_ms - last_new_ms >= floor_ms'),
    'the python burst still counts its settled window in frames');
});
