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
# Sets DETECT_MEM_GB (int, accelerator memory) and DETECT_KIND (nvidia|amd|apple|cpu).
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

  # AMD ROCm: rocm-smi reports VRAM in bytes (--showmeminfo vram).
  if command -v rocm-smi >/dev/null 2>&1; then
    local bytes
    bytes=$(rocm-smi --showmeminfo vram --csv 2>/dev/null \
            | awk -F, 'NR>1 && $2 ~ /^[0-9]+$/ {print $2}' | sort -rn | head -1 || echo 0)
    if [[ -n "$bytes" && "$bytes" -gt 0 ]]; then
      DETECT_MEM_GB=$(( bytes / 1024 / 1024 / 1024 ))
      DETECT_KIND="amd"
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

# ── Speed advisory ──────────────────────────────────────────────────────────
# LLM token generation is MEMORY-BANDWIDTH bound: each decoded token streams the
# active weights once, so tok/s ≈ bandwidth ÷ bytes-read-per-token. For this 9B
# model that's ~9 GB/token on 8-bit (FP8) and ~4.5 GB/token on 4-bit (AWQ); real
# throughput lands near ~50% of the theoretical ceiling. So we estimate speed
# from the device's MEMORY BANDWIDTH (not its capacity), which the capacity gate
# can't see. Calibration (measured on a 5090, 1792 GB/s): FP8 ≈ 100 tok/s,
# AWQ ≈ 200 tok/s — both fall out of the formula below.
#
# Bandwidth (GB/s) for common devices, keyed by a substring of the reported name.
# Unknown devices fall back to the memory-tier heuristic (Apple / AWQ ⇒ likely slow).
BW_EFFICIENCY_PCT=50      # fraction of theoretical bandwidth realized in practice
GB_PER_TOKEN_FP8=9        # ~bytes streamed per decoded token, 8-bit weights
GB_PER_TOKEN_AWQ_X10=45   # 4-bit ≈ 4.5 GB/token, ×10 so we keep integer math
SLOW_TOKENS_PER_SEC=30    # below this, self-hosting feels sluggish → suggest cloud

# Echo the device's memory bandwidth in GB/s for a given name, or nothing if unknown.
# Figures are spec-sheet peak VRAM / unified-memory bandwidth.
device_bandwidth_gbs() {
  local name="$1"
  case "$name" in
    # ── NVIDIA datacenter ──
    *H200*)                 echo 4800 ;;   # H200 (HBM3e)
    *H100*)                 echo 3350 ;;   # H100 (HBM3)
    *A100*)                 echo 2000 ;;   # A100 (HBM2e)
    *L40*)                  echo 864  ;;   # L40 / L40S
    *A6000*|*"RTX 6000"*)   echo 768  ;;   # A6000 / RTX 6000 Ada
    *A10*)                  echo 600  ;;   # A10
    *T4*)                   echo 320  ;;   # T4
    # ── NVIDIA RTX 50 (GDDR7) ──
    *5090*)                 echo 1792 ;;
    *"5080"*)               echo 960  ;;
    *"5070 Ti"*)            echo 896  ;;
    *5070*)                 echo 672  ;;
    *5060*)                 echo 448  ;;
    # ── NVIDIA RTX 40 ──
    *4090*)                 echo 1008 ;;
    *4080*)                 echo 717  ;;   # 4080 / 4080 Super
    *"4070 Ti"*)            echo 672  ;;
    *4070*)                 echo 504  ;;
    *4060*)                 echo 272  ;;
    # ── NVIDIA RTX 30 ──
    *3090*)                 echo 936  ;;   # 3090 / 3090 Ti
    *3080*)                 echo 760  ;;   # 3080 (10G) / 3080 Ti ~912, use conservative
    *3070*)                 echo 448  ;;
    *3060*)                 echo 360  ;;
    # ── AMD Radeon RX 7000 / 6000 (RDNA3/2) ──
    *"7900 XTX"*)           echo 960  ;;
    *"7900 XT"*)            echo 800  ;;
    *"7800 XT"*)            echo 624  ;;
    *"7700 XT"*)            echo 432  ;;
    *"6950 XT"*|*"6900 XT"*) echo 512 ;;
    *"6800 XT"*|*"6800"*)   echo 512  ;;
    *"7600"*|*"6700 XT"*)   echo 384  ;;
    # ── Apple silicon (unified memory bandwidth) ──
    # NB: order matters — match "<chip> Max/Pro/Ultra" before the bare "<chip>".
    *"M3 Ultra"*)           echo 800  ;;
    *"M2 Ultra"*)           echo 800  ;;
    *"M5 Max"*)             echo 614  ;;
    *"M4 Max"*)             echo 546  ;;
    *"M3 Max"*)             echo 400  ;;
    *"M2 Max"*)             echo 400  ;;
    *"M1 Max"*)             echo 400  ;;
    *"M5 Pro"*)             echo 307  ;;
    *"M4 Pro"*)             echo 273  ;;
    *"M3 Pro"*)             echo 150  ;;
    *"M2 Pro"*)             echo 200  ;;
    *"M1 Pro"*)             echo 200  ;;
    *"M5"*)                 echo 154  ;;
    *"M4"*)                 echo 120  ;;
    *"M3"*)                 echo 100  ;;
    *"M2"*)                 echo 100  ;;
    *"M1"*)                 echo 68   ;;
    *) echo "" ;;
  esac
}

# Estimated tok/s for a bandwidth + the active quant (integer math).
estimate_tokens_per_sec() {
  local bw="$1"
  if [[ "$QUANT" == "fp8" ]]; then
    echo $(( bw * BW_EFFICIENCY_PCT / 100 / GB_PER_TOKEN_FP8 ))
  else
    # bw * eff% / 100 / (GB_PER_TOKEN_AWQ_X10/10)  ==  bw * eff% * 10 / 100 / X10
    echo $(( bw * BW_EFFICIENCY_PCT * 10 / 100 / GB_PER_TOKEN_AWQ_X10 ))
  fi
}

# Best-effort device name: nvidia-smi (NVIDIA), rocm-smi (AMD), sysctl (Apple).
detect_device_name() {
  if [[ "$DETECT_KIND" == "nvidia" ]] && command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | sort -rn | head -1
  elif [[ "$DETECT_KIND" == "amd" ]] && command -v rocm-smi >/dev/null 2>&1; then
    rocm-smi --showproductname --csv 2>/dev/null \
      | awk -F, 'NR>1 {print $2}' | head -1
  elif [[ "$DETECT_KIND" == "apple" ]]; then
    sysctl -n machdep.cpu.brand_string 2>/dev/null
  fi
}

speed_advisory() {
  local name bw est
  name="$(detect_device_name)"
  bw="$(device_bandwidth_gbs "$name")"

  if [[ -n "$bw" ]]; then
    est="$(estimate_tokens_per_sec "$bw")"
    if (( est >= SLOW_TOKENS_PER_SEC )); then
      ok "Estimated speed: ~${est} tokens/sec (${name:-device}, ${QUANT}, ~${bw} GB/s) — fast enough to self-host."
      return 0
    fi
    echo
    warn "Estimated speed: ~${est} tokens/sec (${name:-device}, ${QUANT}, ~${bw} GB/s) — below"
    warn "~${SLOW_TOKENS_PER_SEC}/s, so each solve will feel slow. Your card may be too slow to self-host comfortably."
  else
    # Unknown device → fall back to the memory-tier heuristic.
    if [[ "$DETECT_KIND" != "apple" && "$QUANT" != "awq" ]]; then
      return 0   # FP8-capable, non-Apple, unknown card: assume OK, stay quiet.
    fi
    echo
    warn "This setup (${DETECT_KIND}, ${QUANT}) will likely generate well under"
    warn "~${SLOW_TOKENS_PER_SEC} tokens/sec, so each solve will feel slow."
  fi
  echo "  $(c '1;36' '☁')  Want ~100 tok/s without running a GPU? A hosted cloud API (8-bit"
  echo "     model) is coming — star the repo to be notified at launch:"
  echo "     $REPO_URL"
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
  speed_advisory
  print_serve_hint "$BASE"
fi
echo
ok "Done. Self-hosted only for now — hosted cloud API coming: $REPO_URL"
