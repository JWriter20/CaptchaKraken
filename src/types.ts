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
   *  - `drag`     : after a drag was executed
   *  - `wait`     : after a CLI-requested wait elapsed
   *  - `submit`   : after the Verify/Next/Submit button was clicked
   *  - `round`    : a dynamic reCAPTCHA 3x3 round boundary (pre-solve snapshot)
   */
  stage: 'initial' | 'click' | 'drag' | 'wait' | 'submit' | 'round';
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
   * `CaptchaKraken-cli/` directory shipped inside this npm package.
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
}

export interface BoundingBox {
  0: number; // min_x
  1: number; // min_y
  2: number; // max_x
  3: number; // max_y
}

export interface ClickAction {
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

export interface DragAction {
  action: 'drag';
  source_bounding_box: [number, number, number, number];
  target_bounding_box: [number, number, number, number];
}

export interface DoneAction {
  action: 'done';
}

export interface WaitAction {
  action: 'wait';
  duration_ms: number;
}

export type CaptchaAction = ClickAction | WaitAction | DragAction | DoneAction;

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
