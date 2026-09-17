#!/bin/bash
set -eux
# bitsandbytes >=0.44 imports triton_based_modules unconditionally at
# `import bitsandbytes` — even for plain LoRA — and Triton JIT-compiles a
# CUDA util on first import, which needs a C compiler the base training
# image doesn't ship. Without this, every job (LoRA and Full alike) dies at
# trainer.train()/get_peft_model() with "Failed to find C compiler."
(apt-get update -qq && apt-get install -y -qq --no-install-recommends git gcc g++) \
  || (command -v conda >/dev/null 2>&1 && conda install -y -q gcc_linux-64 gxx_linux-64)

# trl>=1.4: assistant_only_loss auto-patches chat templates for the whole
# catalog (Qwen2.5 / Qwen3 / Llama 3.x) and packing preserves assistant masks.
# transformers>=5.2: Qwen3.5 / Gemma4 multimodal architectures.
# bitsandbytes: paged_adamw_8bit optimizer for full fine-tuning.
pip install "trl==1.10.0" "peft>=0.17.0" "transformers>=5.2.0" "accelerate" "qwen-vl-utils" "bitsandbytes>=0.44.0"

# Resolve train_image from modal_shared.modelfam (same source Modal images
# use). Falls back to UNSLOTH_IMAGE env, then "default". Keep pins in sync
# with modal_shared/images/train.py.
_IMG="${UNSLOTH_IMAGE:-}"
if [ -z "${_IMG}" ] && [ -n "${MODEL_ID:-}" ]; then
  _IMG="$(MODEL_ID="$MODEL_ID" python -c '
import os, sys
from pathlib import Path
for root in (Path("/root"), Path(".")):
    if (root / "modal_shared" / "modelfam").is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))
from modal_shared.modelfam import resolve
print(resolve(os.environ["MODEL_ID"]).train_image)
')"
fi
_IMG="${_IMG:-default}"
if [ "${_IMG}" = "gemma4" ] || [ "${_IMG}" = "nemotron35" ]; then
  # Torch 2.10 + unsloth cu128-torch2100 — torchao needs ScalingType (torch≥2.10).
  # Force transformers≥5.10 after Unsloth (its pin is ~5.5; 12B needs gemma4_unified).
  pip install "torch==2.10.0" --index-url https://download.pytorch.org/whl/cu128
  pip install "unsloth[cu128-torch2100]==2026.8.5" "unsloth_zoo==2026.8.4"
  pip install "transformers>=5.10.2"
elif [ "${_IMG}" = "gpt_oss" ]; then
  # Keep Unsloth's transformers pin — 5.10+ breaks MoE expert weight load.
  # kernels prevents silent MXFP4→bf16 dequant; <0.15 required for tf≤5.5.
  pip install "torch==2.10.0" --index-url https://download.pytorch.org/whl/cu128
  pip install "unsloth[cu128-torch2100]==2026.8.5" "unsloth_zoo==2026.8.4"
  pip install "kernels>=0.12.0,<0.15"
else
  pip install "unsloth[cu128-torch270]==2026.7.5" "unsloth_zoo==2026.7.6"
fi
# Pretok's Path A (assistant-only labels via return_assistant_tokens_mask)
# needs trl>=1.4's chat_template_utils, which Unsloth's own pinned trl<=0.24
# doesn't ship — vendor a checkout and point TRL_SRC at it. Falls back to
# Path B (multi-header masker) if this ever breaks.
git clone --depth 1 --branch v1.10.0 https://github.com/huggingface/trl.git /tmp/trl_src
export TRL_SRC=/tmp/trl_src

python -u train.py
