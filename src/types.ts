export interface CaptchaKrakenConfig {
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
