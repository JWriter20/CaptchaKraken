#!/usr/bin/env bash
#
# install.sh — one-command, hardware-gated setup for the self-hosted
# CaptchaKraken grid solver.
#
# What it does:
#   1. Detects your accelerator memory (NVIDIA VRAM, or Apple-silicon unified
#      memory) and picks the right base-model quantization for it.
#   2. Refuses to install a model your hardware can't actually serve, and instead
#      offers you two clear options.
#   3. Downloads the chosen base model + the grid LoRA from HuggingFace.
#   4. Writes a sourceable `captchakraken.env` so the solver class picks up the
#      endpoint and LoRA name automatically.
#
# Usage:
#   bash install.sh                 # auto-detect + install
#   bash install.sh --quant fp8     # force 8-bit FP8 (best accuracy)
#   bash install.sh --quant awq     # force 4-bit AWQ (lighter)
#   bash install.sh --download-only # skip the hardware gate, just fetch weights
#   bash install.sh --yes           # non-interactive (accept recommended path)
#
# This is the SELF-HOSTED path. A hosted cloud API (no GPU required) is coming —
# watch the repo: https://github.com/JWriter20/PlaywrightCaptchaKrakenJS
set -euo pipefail

# ── Model registry ──────────────────────────────────────────────────────────
BASE_FP8="RedHatAI/Qwen3.5-9B-FP8-dynamic"    # 8-bit, ~14 GB weights, best vision-LoRA accuracy
BASE_AWQ="cyankiwi/Qwen3.5-9B-AWQ-4bit"       # 4-bit, ~6 GB weights, lighter / lower accuracy
GRID_LORA="JobHarvest/qwen3.5-9b-grid-lora"   # the captcha grid adapter (served as `captcha-grid`)
LORA_NAME="captcha-grid"
PORT="${VLLM_PORT:-8000}"
REPO_URL="https://github.com/JWriter20/PlaywrightCaptchaKrakenJS"

# Memory floors (GB). FP8 keeps the vision tower in bf16 → needs more headroom
# but scores higher; AWQ is the budget path. Below AWQ_MIN we won't serve.
FP8_MIN=22      # comfortable FP8 serve (weights + KV cache + bf16 ViT + LoRA)
AWQ_MIN=11      # comfortable AWQ serve
HARD_FLOOR=$AWQ_MIN

QUANT=""            # "" = auto
DOWNLOAD_ONLY=0
ASSUME_YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quant) QUANT="${2:-}"; shift 2 ;;
    --quant=*) QUANT="${1#*=}"; shift ;;
    --download-only) DOWNLOAD_ONLY=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

c() { printf '\033[%sm%s\033[0m' "$1" "$2"; }   # color helper
info()  { echo "$(c '1;36' '➜') $*"; }
ok()    { echo "$(c '1;32' '✓') $*"; }
warn()  { echo "$(c '1;33' '!') $*"; }
err()   { echo "$(c '1;31' '✗') $*" >&2; }

# ── Hardware detection ──────────────────────────────────────────────────────
# Sets DETECT_MEM_GB (int, accelerator memory) and DETECT_KIND (nvidia|apple|cpu).
detect_hardware() {
  DETECT_MEM_GB=0
  DETECT_KIND="cpu"

  if command -v nvidia-smi >/dev/null 2>&1; then
    # Largest single-GPU memory (vLLM serves one model per GPU by default).
    local mib
    mib=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null \
          | sort -rn | head -1 || echo 0)
    if [[ -n "$mib" && "$mib" -gt 0 ]]; then
      DETECT_MEM_GB=$(( mib / 1024 ))
      DETECT_KIND="nvidia"
      return
    fi
  fi

  # Apple silicon: unified memory == system RAM, reported by sysctl.
  if [[ "$(uname -s)" == "Darwin" ]] && [[ "$(uname -m)" == "arm64" ]]; then
    local bytes
    bytes=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
    if [[ "$bytes" -gt 0 ]]; then
      DETECT_MEM_GB=$(( bytes / 1024 / 1024 / 1024 ))
      DETECT_KIND="apple"
      return
    fi
  fi

  # Fallback: system RAM (CPU-only / unrecognised accelerator).
  if [[ -r /proc/meminfo ]]; then
    local kb
    kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
    DETECT_MEM_GB=$(( kb / 1024 / 1024 ))
  fi
}

# ── Insufficient-hardware menu ──────────────────────────────────────────────
insufficient_menu() {
  err "Detected ~${DETECT_MEM_GB} GB of ${DETECT_KIND} memory — below the ${HARD_FLOOR} GB"
  err "floor needed to serve even the 4-bit model with acceptable headroom."
  echo
  echo "You have two options:"
  echo "  $(c '1;37' '1)') Download the weights anyway (no serve check) — useful if you're"
  echo "     staging on a dev box to copy onto a bigger server later."
  echo "  $(c '1;37' '2)') Open the GitHub repo to get notified when we ship smaller models"
  echo "     and a hosted cloud API (both coming soon) — no GPU required:"
  echo "     $REPO_URL"
  echo
  if [[ "$ASSUME_YES" == "1" ]]; then
    warn "--yes set; defaulting to option 2 (exit without download)."
    exit 1
  fi
  read -r -p "Choose [1/2] (default 2): " choice
  case "${choice:-2}" in
    1) info "Proceeding with download-only (AWQ 4-bit)."; QUANT="awq"; DOWNLOAD_ONLY=1 ;;
    *) info "Opening repo. Star it to be notified at launch: $REPO_URL"; exit 0 ;;
  esac
}

# ── Pick quantization from detected memory ──────────────────────────────────
pick_quant() {
  if [[ -n "$QUANT" ]]; then
    info "Quantization forced to: $QUANT"
    return
  fi
  if (( DETECT_MEM_GB >= FP8_MIN )); then
    QUANT="fp8"
    ok "~${DETECT_MEM_GB} GB ${DETECT_KIND} → 8-bit FP8 (best accuracy, matches our hosted config)."
  elif (( DETECT_MEM_GB >= AWQ_MIN )); then
    QUANT="awq"
    ok "~${DETECT_MEM_GB} GB ${DETECT_KIND} → 4-bit AWQ (lighter; ~5pp lower accuracy than FP8)."
  else
    insufficient_menu
  fi
}

# ── Download weights ────────────────────────────────────────────────────────
hf_bin() {
  if command -v hf >/dev/null 2>&1; then echo "hf"; return; fi
  if command -v huggingface-cli >/dev/null 2>&1; then echo "huggingface-cli"; return; fi
  err "No HuggingFace CLI found. Install it with:  pip install -U 'huggingface_hub[cli]'"
  exit 1
}

download() {
  local base="$1" hf
  hf="$(hf_bin)"
  info "Downloading base model: $base"
  "$hf" download "$base" >/dev/null
  info "Downloading grid LoRA:  $GRID_LORA"
  "$hf" download "$GRID_LORA" >/dev/null
  ok "Weights cached under ${HF_HOME:-$HOME/.cache/huggingface}."
}

# ── Write the sourceable env file ───────────────────────────────────────────
write_env() {
  local base="$1"
  local key
  key="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' 2>/dev/null || echo changeme)"
  cat > captchakraken.env <<EOF
# captchakraken.env — generated by install.sh ($(date -u +%Y-%m-%dT%H:%M:%SZ))
# Source this before running the solver:  source captchakraken.env
#
# Two vars, and that's it:
#   VLLM_BASE_URL          — where the solver sends requests (your local server,
#                            or the hosted cloud endpoint once it launches).
#   CAPTCHA_KRAKEN_API_KEY — bearer token. For self-hosting it's whatever you set
#                            on your vLLM server; for the hosted API (coming soon)
#                            it'll be your account key.
export VLLM_BASE_URL="http://localhost:${PORT}/v1"
export CAPTCHA_KRAKEN_API_KEY="${key}"

# Reference for starting your own vLLM server (LoRA name + base are baked into
# the solver defaults — you don't need to export them):
#   BASE_MODEL=${base}
#   GRID_LORA=${GRID_LORA}   served as: ${LORA_NAME}
EOF
  ok "Wrote captchakraken.env (VLLM_BASE_URL, CAPTCHA_KRAKEN_API_KEY)."
}

print_serve_hint() {
  local base="$1"
  echo
  info "To serve locally (requires vLLM ≥ 0.20.1 with LoRA support):"
  cat <<EOF
    source captchakraken.env
    # The server reads VLLM_API_KEY as its bearer; set it to the same value as
    # CAPTCHA_KRAKEN_API_KEY so the solver can authenticate:
    export VLLM_API_KEY="\$CAPTCHA_KRAKEN_API_KEY"
    vllm serve "${base}" \\
      --reasoning-parser qwen3 \\
      --enable-lora --enable-tower-connector-lora \\
      --max-lora-rank 64 --max-model-len 65536 \\
      --gpu-memory-utilization 0.80 --trust-remote-code \\
      --port ${PORT} \\
      --lora-modules ${LORA_NAME}=${GRID_LORA}

  $(c '1;33' 'NB:') --enable-tower-connector-lora is REQUIRED or the vision LoRA is
      silently dropped and accuracy collapses. See docs/CAPABILITIES.md.
EOF
}

# ── Main ────────────────────────────────────────────────────────────────────
echo "$(c '1;35' '╔══ CaptchaKraken self-hosted installer ══╗')"
detect_hardware
info "Detected: ${DETECT_KIND} accelerator, ~${DETECT_MEM_GB} GB memory."

if [[ "$DOWNLOAD_ONLY" == "1" && -z "$QUANT" ]]; then
  QUANT="awq"
  warn "--download-only: skipping hardware gate, defaulting to AWQ 4-bit."
else
  pick_quant
fi

case "$QUANT" in
  fp8) BASE="$BASE_FP8" ;;
  awq) BASE="$BASE_AWQ" ;;
  *)   err "Invalid --quant '$QUANT' (use fp8 or awq)"; exit 2 ;;
esac

download "$BASE"
write_env "$BASE"

if [[ "$DOWNLOAD_ONLY" == "1" ]]; then
  ok "Download-only complete. Copy the HF cache + captchakraken.env to your server."
else
  print_serve_hint "$BASE"
fi
echo
ok "Done. Self-hosted only for now — hosted cloud API coming: $REPO_URL"
