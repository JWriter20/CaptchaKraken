/**
 * Lifecycle event emitted by {@link CaptchaKrakenConfig.onStep}. One is fired
 * before any interaction (`stage: 'initial'`) and one after every executed
 * action (click batch, drag, wait, submit) plus per dynamic-grid round, so a
 * caller can record the exact sequence of intermediate stages, time them, and
 * count steps without scraping CAPTCHA_DEBUG dumps.
 */
export interface SolveStepEvent {
  /** 1-based monotonically increasing step index across the whole solve. */
  index: number;
  /**
   * Coarse stage kind:
   *  - `initial`  : screenshot taken before any action (baseline)
   *  - `click`    : after a click (or batch of clicks) was executed
   *  - `drag`     : after a drag was executed (a slider counts as one)
   *  - `type`     : after a distorted-text answer was typed into its box
   *  - `wait`     : after a CLI-requested wait elapsed
   *  - `submit`   : after the Verify/Next/Submit button was clicked
   *  - `round`    : a dynamic reCAPTCHA 3x3 round boundary (pre-solve snapshot)
   */
  stage: 'initial' | 'click' | 'drag' | 'type' | 'wait' | 'submit' | 'round';
  /** Short human label, e.g. "round-2:clicked 3 tile(s)". */
  label: string;
  /**
   * Absolute path to a PNG screenshot of the captcha element at this step.
   * The file is owned by the callback once emitted — the solver does NOT
   * delete it (copy/move it where you need it). Null if the screenshot failed.
   */
  screenshotPath: string | null;
  /** Detected puzzle vendor, if known. */
  puzzleSource?: 'hcaptcha' | 'recaptcha' | 'unknown';
  /**
   * Which captcha frame this screenshot is of:
   *  - `checkbox`  : the anchor "I'm not a robot" widget (no puzzle yet)
   *  - `challenge` : the open image/grid challenge frame (the real puzzle)
   *  - `unknown`   : could not be determined from the frame src
   * Recorders that only want the actual solve (not the pre-challenge checkbox
   * clicks) filter to `challenge`.
   */
  frameRole?: 'checkbox' | 'challenge' | 'unknown';
  /** Outer solve-loop attempt this step belongs to. */
  attempt: number;
  /** ms since solve() started. */
  elapsedMs: number;
  /** Free-form per-stage detail (action payload, clicked cells, etc.). */
  meta?: Record<string, any>;
}

export interface CaptchaKrakenConfig {
  /**
   * Optional observer fired at each intermediate solve stage. Receives a
   * baseline screenshot before any action and one after every executed action.
   * Use it to capture intermediate-stage screenshots, count steps, and time
   * each phase. The callback may be async; the solver awaits it. Errors thrown
   * by the callback are swallowed (never fail a solve because logging failed).
   *
   * The PNG at `event.screenshotPath` is owned by the callback once emitted —
   * the solver will not delete it.
   */
  onStep?: (event: SolveStepEvent) => void | Promise<void>;

  /**
   * Path to the bundled CaptchaKraken CLI root.
   *
   * Usually you do NOT need to set this. If omitted, the solver will auto-resolve the
   * `python/` directory (the captchakraken package) shipped inside this npm package.
   */
  repoPath?: string;
  /**
   * Command to run python (default: 'python' or 'python3').
   */
  pythonCommand?: string;
  /**
   * vLLM LoRA name to invoke (default: 'captcha' — our bbox-aware LoRA).
   * Override if you've registered a different module with the vLLM server.
   */
  model?: string;
  /**
   * Bearer token for the vLLM server (also picked up from VLLM_API_KEY env).
   */
  apiKey?: string;

  /**
   * Starting mouse position (default: { x: 100, y: 100 }).
   * HIGHLY RECOMMENDED to set this, prevents jumping around of the cursor when solving.
   */
  startingMousePosition?: { x: number, y: number };


  /**
   * Automatically re-check for newly opened / next-step captchas after each solve
   * attempt (e.g., clicking a checkbox opens an image challenge).
   *
   * Default: 10
   */
  maxSolveLoops?: number;

  /**
   * Delay (ms) after executing actions before re-detecting captchas.
   * Useful to allow challenge frames / new images to appear.
   *
   * Default: 1200
   */
  postSolveDelayMs?: number;

  /**
   * Overall time limit (ms) for the entire solve loop.
   *
   * Default: 120000 (2 minutes)
   */
  overallSolveTimeoutMs?: number;

  /**
   * Poll interval (ms) for the reCAPTCHA grid-cell-load wait — how often the
   * solver re-screenshots the grid while waiting for tiles to stop fading in.
   * Doubles as the inter-frame gap for the settle change-detector, so keep it
   * comfortably above zero.
   *
   * Default: 250
   */
  gridLoadPollIntervalMs?: number;

  /**
   * Overall timeout (ms) for the reCAPTCHA grid-cell-load wait. On timeout the
   * solver proceeds to screenshot anyway (best-effort).
   *
   * Default: 8000
   */
  gridLoadTimeoutMs?: number;

  /**
   * reCAPTCHA 3x3 dynamic puzzles only. After clicking a round of tiles, the
   * max time (ms) to wait for at least one clicked blank/fading tile to finish
   * loading before re-screenshotting and re-solving. On timeout the solver
   * proceeds anyway (best-effort, backstopped by overallSolveTimeoutMs).
   *
   * Default: 6000
   */
  recaptchaDynamicFadeWaitMs?: number;

  /**
   * reCAPTCHA 3x3 dynamic puzzles only. Minimum gap (ms) between the two frames
   * the fade detectors diff, and the poll cadence for waitForAnyClickedTileLoaded
   * / currentLoadingCells. Kept comfortably above zero so two consecutive frames
   * during a slow fade differ enough for the change detector to fire (frames
   * captured back-to-back can look identical mid-fade and read as "loaded").
   *
   * Default: 250
   */
  recaptchaDynamicFadePollMs?: number;

  /**
   * reCAPTCHA 3x3 dynamic puzzles only. Grace window (ms) after clicking during
   * which the solver watches the clicked tiles for the ONSET of a blank/fade
   * before deciding the puzzle is solved. reCAPTCHA keeps a clicked tile
   * SELECTED (showing its old image + a blue badge) for a couple of seconds and
   * only THEN blanks it to swap in a replacement, so the window must comfortably
   * exceed that delay or we submit while the refresh is still pending. If no
   * clicked tile goes blank/changing within this window, the puzzle is treated
   * as solved.
   *
   * Default: 4000
   */
  recaptchaFadeOnsetGraceMs?: number;

  /**
   * reCAPTCHA 3x3 dynamic puzzles only. Cap on the number of
   * click → refresh → re-solve rounds within a single puzzle, independent of
   * maxSolveLoops.
   *
   * Default: 8
   */
  recaptchaMaxDynamicRounds?: number;

  /**
   * reCAPTCHA 3x3 dynamic puzzles only. When true, the solver hovers the mouse
   * over the just-clicked blank/fading tiles (in click order) while waiting for
   * them to reload, mimicking a human. Disable to skip the hover behavior.
   *
   * Default: true
   */
  recaptchaTileHoverEnabled?: boolean;

  /**
   * While the model is generating a solution (the main idle window), drift the
   * cursor over the challenge area with human-like trajectories instead of
   * leaving it frozen. Cancelled the instant the model responds. Set false to
   * keep the cursor still during inference.
   *
   * Default: true
   */
  idleMouseWander?: boolean;

  /**
   * After an action, the max time (ms) to watch for the solve outcome — the
   * vendor's solved signal (checkbox checked / response token) or a freshly
   * rendered next round — before falling back to a full re-detect. Returning as
   * soon as the solved signal appears avoids re-entering the solve pipeline on a
   * challenge frame that is merely animating closed.
   *
   * Default: 4000
   */
  postSolveOutcomeTimeoutMs?: number;

  /**
   * Guard against the captcha frame changing WHILE the model is generating.
   * reCAPTCHA/hCaptcha fade fresh tiles in over ~1s; if new imagery paints
   * between the screenshot we send the model and the moment its answer returns,
   * that answer describes a stale ("undeveloped") frame and its tile picks /
   * bboxes no longer line up with what's on screen. When enabled, the solver
   * re-screenshots the frame after inference, and if it changed it discards the
   * stale answer and re-solves on the fresh frame (see maxStaleFrameReSolves).
   *
   * Default: true
   */
  staleFrameReSolveEnabled?: boolean;

  /**
   * Fraction of pixels (0–1) that must differ between the frame sent to the
   * model and a screenshot taken right after inference for the frame to count
   * as "changed during inference" (a tile faded in / refreshed). Below this,
   * the frame is treated as unchanged and the answer is used as-is. Reuses the
   * same frame-diff primitive as the reCAPTCHA settle detector.
   *
   * Default: 0.02
   */
  staleFrameDiffThreshold?: number;

  /**
   * Max number of times to re-screenshot + re-solve when the frame keeps
   * changing during inference, before giving up and acting on the latest answer
   * (better to act than to spin). Set 0 to detect-and-log without re-solving.
   *
   * Default: 2
   */
  maxStaleFrameReSolves?: number;

  // ── Settle monitor (challenge-state gating) ───────────────────────────────
  // The solver tracks the challenge's lifecycle state and refuses to send a
  // mid-transition / still-loading frame to the model. These tune the pixel
  // "has it settled yet?" monitor that gates that decision (it reuses the same
  // check-movement frame-diff as the stale-frame guard).

  /**
   * Fraction of pixels (0–1) that must differ between two consecutive challenge
   * frames for it to still count as "moving" (loading/animating) rather than
   * settled.
   *
   * Default: 0.01
   */
  settleDiffThreshold?: number;

  /** Poll interval (ms) for the settle monitor. Default: 220 */
  settlePollMs?: number;

  /**
   * Consecutive still frame-pairs required before the challenge is declared
   * settled and safe to screenshot for the model.
   *
   * Default: 2
   */
  settleFrames?: number;

  /** Overall timeout (ms) for the settle monitor before it gives up. Default: 9000 */
  settleTimeoutMs?: number;

  /**
   * If the challenge keeps changing continuously for at least this long without
   * ever settling, it's treated as an **animated / video** challenge (surfaced
   * distinctly, not as "unsupported"). Static grids settle in ~1–2s; a video
   * never does.
   *
   * Default: 4500
   */
  animatedChallengeAfterMs?: number;

  // ── Animated challenges ───────────────────────────────────────────────────
  // A challenge that never settles is animated BY DESIGN (hCaptcha fades sprites
  // on independent cycles; GeeTest's svg board cycles its glyphs). Those are
  // RECORDED and solved from keyframes rather than abandoned.

  /**
   * Record and solve animated challenges. When false, a challenge that never
   * settles fails with `.animated = true` as it did before — for callers who
   * would rather fail fast than spend the recording time.
   *
   * Default: true
   */
  videoSolveEnabled?: boolean;

  /**
   * Length (ms) and frame rate of the burst recorded from an animated challenge.
   *
   * Do not tune these casually. They are deliberately identical to the geometry
   * the training corpus was collected at (4000ms @ 10fps), and the keyframe
   * slicer reads the clip's temporal structure — a different length or rate
   * changes where a cycle's period lands across the frames, so the live set gets
   * sliced differently from the trained set and the model's frame number stops
   * meaning what the driver thinks it means.
   *
   * Defaults: 4000 / 10
   */
  videoBurstDurationMs?: number;
  videoBurstFps?: number;

  /**
   * How long (ms) to wait for the widget to return to the keyframe the model
   * chose, before clicking anyway; and the poll interval while waiting.
   *
   * Bounded because the alternative is worse: these puzzles cycle, so the state
   * does come back — but if the recording caught a one-off transition it never
   * will, and a click on the model's coordinates is a better use of the
   * remaining budget than a timeout.
   *
   * Defaults: 6000 / 120
   */
  keyframeWaitTimeoutMs?: number;
  keyframeWaitPollMs?: number;

  /**
   * After clicking Submit/Verify, the solver EXPECTS the frame to change (advance
   * to the next round, or close because it was accepted). This is how long (ms)
   * to wait for that transition to begin before re-evaluating — so the shift
   * itself is never screenshotted and mis-read as a fresh (blank) puzzle.
   *
   * Default: 4000
   */
  postSubmitChangeTimeoutMs?: number;

  /**
   * Per-call timeout (ms) for element screenshots of the challenge iframe.
   * Playwright's default is 30000, which means a stale/transitioning handle
   * (e.g. hCaptcha swapped in the next round while we held the old iframe)
   * hangs the whole solve for 30s before failing. Bounding it lets a stale
   * handle fail fast so the solve loop can re-detect the fresh challenge.
   *
   * Default: 8000
   */
  elementScreenshotTimeoutMs?: number;

  /**
   * After a submit, hCaptcha may replace the challenge iframe for the next
   * round, detaching the handle we just detected ("element is not visible").
   * This is a transition, not a dead puzzle: how many times to back off,
   * re-detect the fresh challenge, and retry before giving up.
   *
   * Default: 3
   */
  maxStaleElementRetries?: number;

  /**
   * Backoff (ms) before re-detecting after a stale-element screenshot failure,
   * giving the round transition time to finish.
   *
   * Default: 900
   */
  staleElementBackoffMs?: number;

  /**
   * When the model reports "unsupported" *after we've already interacted* (i.e.
   * mid multi-round), it's almost always a not-yet-settled next round rather
   * than a genuinely unsupported puzzle. This is how many times to wait for the
   * challenge to settle and re-solve before giving up. (A single retry loses a
   * race when the next round loads slowly.)
   *
   * Default: 3
   */
  maxUnsupportedReSolves?: number;
}

export interface BoundingBox {
  0: number; // min_x
  1: number; // min_y
  2: number; // max_x
  3: number; // max_y
}

/**
 * Fields present ONLY on actions from an animated challenge.
 *
 * Why an action carries a picture: on an animated puzzle the target is visible
 * only part of the time, so the coordinates are correct only while the widget
 * looks the way it did in that keyframe. The driver holds the mouse until the
 * live neighbourhood around the click point matches the same neighbourhood of
 * `await_keyframe`, then clicks. Without the wait, a click on a fading sprite
 * lands on background.
 *
 * Both are set together or not at all — a number with no image cannot be waited
 * on, and an image with no number cannot be reported. Absent on every still
 * puzzle, where there is no moment to wait for.
 */
export interface AnimatedActionFields {
  /** Absolute path to the keyframe the model chose to act on. */
  await_keyframe?: string | null;
  /** Its 1-based number in the keyframe set that was sent. */
  frame?: number | null;
}

export interface ClickAction extends AnimatedActionFields {
  action: 'click';
  /**
   * One or more normalized [x1, y1, x2, y2] bboxes (0–1 fractions of the
   * screenshot). Each entry produces one click. Emitted by v2 CLI for both
   * grid selections and click-puzzle points.
   */
  target_bounding_boxes?: Array<[number, number, number, number]>;
  /** Legacy v1 fields kept for backwards-compat with older CLI builds. */
  target_number?: number | null;
  target_bounding_box?: [number, number, number, number] | null;
  target_coordinates?: [number, number] | null;
}

export interface DragAction extends AnimatedActionFields {
  action: 'drag';
  /**
   * Null on a PUZZLE-PIECE SLIDER. Not a missing field — the shape of the
   * answer: what you grab (a handle, elsewhere on the widget) is not what has
   * to arrive (the piece), and how far one moves the other is a vendor detail
   * no picture reveals. The driver reads a null source as "find the slider and
   * close the loop on the piece" (see executeSlide).
   */
  source_bounding_box: [number, number, number, number] | null;
  target_bounding_box: [number, number, number, number];
}

/** Distorted-text captchas: the answer is a string, not a place. */
export interface TypeAction {
  action: 'type';
  text: string;
}

export interface DoneAction {
  action: 'done';
}

export interface WaitAction {
  action: 'wait';
  duration_ms: number;
}

export type CaptchaAction = ClickAction | WaitAction | DragAction | TypeAction | DoneAction;

export type SolverResult = CaptchaAction | CaptchaAction[];

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens?: number;
  model: string;
}

export interface CliResponse {
  actions: SolverResult;
  token_usage: TokenUsage[];
}

export interface Vector {
  x: number;
  y: number;
}

export interface SolveResult {
  isSolved: boolean;
  finalMousePosition: Vector;
  tokenUsage: {
    modelName: string;
    inputTokens: number;
    outputTokens: number;
    cachedInputTokens: number;
    estimatedCost: number;
  };
}
