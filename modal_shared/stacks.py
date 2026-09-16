"""Immutable train/serve stacks. FamilySpec points here; never edit a live pin set.

Add a model:
  1. Existing stack works → FamilySpec patterns (+ models.json). Stop.
  2. Need new Unsloth/torch/transformers/vLLM pins → new stack key, new Image,
     generate Function/Cls, canary, then point only that family at it.
  3. Never change pip/docker pins on an existing key. Clone → new key.

Deployed Modal names equal the stack id (``sft_u2026_7``, ``L4_vllm``). One
Function per Image — families sharing pins share the Function.

Stdlib only — imported by Django, Modal, and tests.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- train stacks (FamilySpec.train_image) ---------------------------------

TRAIN_U2026_7 = "u2026_7"
TRAIN_U2026_8_TF510 = "u2026_8_tf510"
TRAIN_U2026_8_TF515 = "u2026_8_tf515"
TRAIN_U2026_8_GPOS = "u2026_8_gptoss"
TRAIN_U2026_8_18 = "u2026_8_18"
TRAIN_U2026_9_2 = "u2026_9_2"

TRAIN_STACKS: tuple[str, ...] = (
    TRAIN_U2026_7,
    TRAIN_U2026_8_TF510,
    TRAIN_U2026_8_TF515,
    TRAIN_U2026_8_GPOS,
    TRAIN_U2026_8_18,
    TRAIN_U2026_9_2,
)

# Historical FamilySpec / models.json keys. Lookup must normalize.
TRAIN_STACK_ALIASES: dict[str, str] = {
    "default": TRAIN_U2026_8_18,
    "gemma4": TRAIN_U2026_8_TF510,
    "nemotron35": TRAIN_U2026_8_TF510,
    "muse": TRAIN_U2026_8_TF515,
    "gpt_oss": TRAIN_U2026_8_GPOS,
    TRAIN_U2026_7: TRAIN_U2026_7,
    TRAIN_U2026_8_TF510: TRAIN_U2026_8_TF510,
    TRAIN_U2026_8_TF515: TRAIN_U2026_8_TF515,
    TRAIN_U2026_8_GPOS: TRAIN_U2026_8_GPOS,
    TRAIN_U2026_8_18: TRAIN_U2026_8_18,
    TRAIN_U2026_9_2: TRAIN_U2026_9_2,
}

# Deployed Modal Function = sft_{stack}. One name per Image.
TRAIN_FUNCTION_NAMES: dict[str, str] = {s: f"sft_{s}" for s in TRAIN_STACKS}

TRAIN_STACK_PINS: dict[str, str] = {
    TRAIN_U2026_7: "unsloth[cu128-torch270]==2026.7.5 / torch==2.7.0 / transformers>=5.2 / trl v1.10.0",
    TRAIN_U2026_8_TF510: "unsloth[cu128-torch2100]==2026.8.5 / torch==2.10.0 / transformers>=5.10.2 / trl v1.10.0",
    TRAIN_U2026_8_TF515: "unsloth[cu128-torch2100]==2026.8.5 / torch==2.10.0 / transformers>=5.15 / trl v1.10.0",
    TRAIN_U2026_8_GPOS: "unsloth[cu128-torch2100]==2026.8.5 / torch==2.10.0 / Unsloth transformers pin / kernels>=0.12,<0.15 / trl v1.10.0",
    TRAIN_U2026_8_18: "unsloth[cu128-torch2100]==2026.8.18 / unsloth_zoo==2026.8.12 / torch==2.10.0 / transformers>=5.10.2 / trl v1.10.0",
    TRAIN_U2026_9_2: "unsloth[cu128-torch2100]==2026.9.2 / unsloth_zoo==2026.9.1 / torch==2.10.0 / transformers>=5.10.2 / trl v1.10.0",
}


def normalize_train_stack(key: str | None) -> str:
    raw = (key or "").strip()
    if not raw:
        return TRAIN_U2026_8_18
    if raw in TRAIN_STACKS:
        return raw
    return TRAIN_STACK_ALIASES.get(raw) or TRAIN_STACK_ALIASES.get(raw.lower()) or TRAIN_U2026_8_18


def train_function_name(stack_or_alias: str | None) -> str:
    return TRAIN_FUNCTION_NAMES[normalize_train_stack(stack_or_alias)]


# --- serve stacks (FamilySpec.serve_image / routing headers) ---------------
# Public keys stay vllm / muse_glimmer — they are on the wire. New vLLM tag
# = new key (vllm_0_28, …). Never retag `vllm` in place.

SERVE_VLLM = "vllm"
SERVE_MUSE_GLIMMER = "muse_glimmer"

SERVE_STACKS: tuple[str, ...] = (SERVE_VLLM, SERVE_MUSE_GLIMMER)

SERVE_STACK_PINS: dict[str, str] = {
    SERVE_VLLM: (
        "vllm/vllm-openai:v0.27.1 + transformers>=5.10.2,<5.15 + runai-model-streamer>=0.15.7"
    ),
    SERVE_MUSE_GLIMMER: (
        "vllm/vllm-openai:cu129-nightly-46638857fdbb30e0c232c9e8f9cb1ff6d6f545c3"
        " + runai-model-streamer>=0.15.7"
    ),
}

# (gpu_type, serve_image) pairs we deploy. Cls name is ``{gpu}_{serve_image}``
# with hyphens stripped from gpu (A100-80GB → A10080GB_vllm). LoRA pools add
# ``_lora`` so GPU snapshots can be on without snapshotting full-FT workers.
WORKER_ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        ("L4", SERVE_VLLM),
        ("L40S", SERVE_VLLM),
        ("A100-80GB", SERVE_VLLM),
        ("H100", SERVE_VLLM),
        ("H200", SERVE_VLLM),
        ("B200", SERVE_VLLM),
        ("B300", SERVE_VLLM),
        ("A100-80GB", SERVE_MUSE_GLIMMER),
        ("H200", SERVE_MUSE_GLIMMER),
        ("B200", SERVE_MUSE_GLIMMER),
    }
)


def _gpu_token(gpu_type: str) -> str:
    return gpu_type.replace("-", "")


def worker_cls_name(
    gpu_type: str,
    serve_image: str = SERVE_VLLM,
    default: str | None = None,
    *,
    lora: bool = False,
) -> str:
    image = serve_image or SERVE_VLLM
    if (gpu_type, image) not in WORKER_ALLOWED:
        if default is not None and image == SERVE_VLLM:
            name = default
        else:
            raise ValueError(f"no inference worker for gpu={gpu_type!r} serve_image={image!r}")
    else:
        name = f"{_gpu_token(gpu_type)}_{image}"
    return f"{name}_lora" if lora else name


WORKER_CLS: dict[tuple[str, str], str] = {pair: worker_cls_name(*pair) for pair in WORKER_ALLOWED}
WORKER_LORA_CLS: dict[tuple[str, str], str] = {
    pair: worker_cls_name(*pair, lora=True) for pair in WORKER_ALLOWED
}

GPU_CLASS_MAP: dict[str, str] = {
    gpu: cls for (gpu, image), cls in WORKER_CLS.items() if image == SERVE_VLLM
}

SERVE_CLASS_MAP: dict[tuple[str, str], str] = {
    (gpu, image): cls for (gpu, image), cls in WORKER_CLS.items() if image != SERVE_VLLM
}

# scaledown bucket + default max_model_len per GPU (used when generating Cls).
GPU_TIER: dict[str, tuple[str, int]] = {
    "L4": ("short", 8192),
    "L40S": ("short", 16384),
    "A100-80GB": ("large", 16384),
    "H100": ("large", 16384),
    "H200": ("large", 32768),
    "B200": ("large", 16384),
    "B300": ("large", 32768),
}


# --- canaries (prove stacks, not the catalog) ------------------------------


@dataclass(frozen=True)
class TrainCanary:
    stack: str
    hf_model_id: str
    training_type: str  # "Lora" | "Full" — both Unsloth
    params_b: float
    gate: str  # "pr" | "nightly"


TRAIN_CANARIES: tuple[TrainCanary, ...] = (
    # Default cutover target (Qwen / Llama / LFM / …).
    TrainCanary(TRAIN_U2026_8_18, "Qwen/Qwen3-0.6B", "Lora", 0.6, "pr"),
    TrainCanary(TRAIN_U2026_8_18, "Qwen/Qwen3-0.6B", "Full", 0.6, "pr"),
    TrainCanary(TRAIN_U2026_9_2, "Qwen/Qwen3-0.6B", "Lora", 0.6, "pr"),
    TrainCanary(TRAIN_U2026_9_2, "Qwen/Qwen3-0.6B", "Full", 0.6, "pr"),
    TrainCanary(TRAIN_U2026_8_18, "LiquidAI/LFM2.5-230M", "Lora", 0.23, "pr"),
    TrainCanary(TRAIN_U2026_8_18, "LiquidAI/LFM2.5-230M", "Full", 0.23, "pr"),
    # Gemma4 / Nemotron35 stay on tf510 — 8.18 + transformers 5.15 hits
    # AmbiguousGlobalPerLayerAttributeError on Gemma4 head_dim.
    TrainCanary(TRAIN_U2026_8_TF510, "unsloth/gemma-4-E2B-it", "Lora", 5.1, "pr"),
    TrainCanary(TRAIN_U2026_8_TF510, "unsloth/gemma-4-E2B-it", "Full", 5.1, "pr"),
    TrainCanary(TRAIN_U2026_7, "Qwen/Qwen3-0.6B", "Lora", 0.6, "nightly"),
    TrainCanary(TRAIN_U2026_8_TF515, "unsloth/Muse-Glimmer-30B", "Lora", 30.0, "nightly"),
    TrainCanary(
        TRAIN_U2026_8_GPOS, "unsloth/gpt-oss-20b-unsloth-bnb-4bit", "Lora", 21.5, "nightly"
    ),
)
