# CaptchaKraken vLLM server image.
#
# Serves the base model + captcha LoRA over an OpenAI-compatible endpoint on
# :8000. The image bakes in the `captchakraken[serve]` stack and (optionally)
# the weights, then hands off to `captchakraken server run`, which execs
# `vllm serve` assembled entirely from the env-overridable config — so you can
# repoint it at any base model / adapter without editing this file.
#
#   docker build -t captchakraken-vllm .
#   docker run --gpus all -p 8000:8000 --ipc=host \
#     -e VLLM_API_KEY=your_key captchakraken-vllm
FROM pytorch/pytorch:2.7.0-cuda12.8-cudnn9-devel

WORKDIR /app

RUN apt-get update && apt-get install -y \
    git ninja-build libgl1 libglib2.0-0 wget ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install the package + serving extra (vllm/torch/transformers/accelerate/hf).
COPY . /app/captchakraken
RUN pip install --upgrade pip && pip install "/app/captchakraken[serve]"

# Model identity — all overridable at build/run time (model-agnostic image).
ARG HF_TOKEN
ENV HF_TOKEN=${HF_TOKEN}
ENV CAPTCHA_BASE_MODEL="Qwen/Qwen3.5-9B"
ENV CAPTCHA_LORA_ADAPTER="CaptchaKraken/CaptchaKraken_v1"
ENV CAPTCHA_LORA_NAME="captcha"
ENV VLLM_GPU_MEMORY_UTILIZATION="0.80"
ENV VLLM_PORT=8000

# Bake weights into the image (best-effort; falls back to runtime download).
RUN python3 -c "import os; from huggingface_hub import snapshot_download; \
    token = os.getenv('HF_TOKEN'); \
    snapshot_download(os.getenv('CAPTCHA_BASE_MODEL'), token=token); \
    snapshot_download(os.getenv('CAPTCHA_LORA_ADAPTER'), token=token)" \
    || echo "Warning: model prefetch failed; weights download at first run."

EXPOSE 8000
CMD ["captchakraken", "server", "run"]
