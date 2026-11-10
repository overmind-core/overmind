"""Which bases speculate. Absent means the server stays on the base alone."""

from __future__ import annotations

DRAFTS_DIRNAME = ".drafts"

# (steps, top-k, draft tokens). The MTP head is inside the checkpoint.
# Qwen's SGLang recipe, and what Modal's dedicated endpoints launch.
_MTP: dict[str, tuple[int, int, int]] = {
    "Qwen/Qwen3.5-0.8B": (3, 1, 4),
    "unsloth/Qwen3.5-0.8B": (3, 1, 4),
    "Qwen/Qwen3.5-2B": (3, 1, 4),
    "unsloth/Qwen3.5-2B": (3, 1, 4),
    "Qwen/Qwen3.5-4B": (3, 1, 4),
    "unsloth/Qwen3.5-4B": (3, 1, 4),
    "Qwen/Qwen3.5-9B": (3, 1, 4),
    "unsloth/Qwen3.5-9B": (3, 1, 4),
    "Qwen/Qwen3.6-27B": (3, 1, 4),
    "unsloth/Qwen3.6-27B": (3, 1, 4),
}

# (draft repo, num_speculative_tokens). DFlash2 uses the same method
# with 7 draft tokens and needs the dedicated vLLM 0.33 image.
_DRAFTS: dict[str, tuple[str, int]] = {
    "Qwen/Qwen3-4B": ("z-lab/Qwen3-4B-DFlash-b16", 15),
    "unsloth/Qwen3-4B": ("z-lab/Qwen3-4B-DFlash-b16", 15),
    "Qwen/Qwen3-8B": ("z-lab/Qwen3-8B-DFlash-b16", 15),
    "unsloth/Qwen3-8B": ("z-lab/Qwen3-8B-DFlash-b16", 15),
    "meta-llama/Llama-3.1-8B-Instruct": ("z-lab/LLaMA3.1-8B-Instruct-DFlash-UltraChat", 15),
    "unsloth/Llama-3.1-8B-Instruct": ("z-lab/LLaMA3.1-8B-Instruct-DFlash-UltraChat", 15),
    "Qwen/Qwen3.5-27B": ("z-lab/Qwen3.5-27B-DFlash", 15),
    "unsloth/Qwen3.5-27B": ("z-lab/Qwen3.5-27B-DFlash", 15),
    "Qwen/Qwen3-Coder-30B-A3B-Instruct": ("z-lab/Qwen3-Coder-30B-A3B-DFlash", 15),
    "openai/gpt-oss-20b": ("z-lab/gpt-oss-20b-DFlash", 15),
    "unsloth/gpt-oss-20b-BF16": ("z-lab/gpt-oss-20b-DFlash", 15),
    "google/gemma-4-26B-A4B-it": ("z-lab/gemma-4-26B-A4B-it-DFlash", 15),
    "unsloth/gemma-4-26B-A4B-it": ("z-lab/gemma-4-26B-A4B-it-DFlash", 15),
    "Qwen/Qwen3.5-35B-A3B": ("z-lab/Qwen3.5-35B-A3B-DFlash", 15),
    "unsloth/Qwen3.5-35B-A3B": ("z-lab/Qwen3.5-35B-A3B-DFlash", 15),
    "google/gemma-4-31B-it": ("z-lab/gemma-4-31B-it-DFlash", 15),
    "unsloth/gemma-4-31B-it": ("z-lab/gemma-4-31B-it-DFlash", 15),
    "Qwen/Qwen3.8-27B": ("z-lab/Qwen3.8-27B-DFlash2", 7),
    "unsloth/Qwen3.8-27B": ("z-lab/Qwen3.8-27B-DFlash2", 7),
    "unsloth/Muse-Glimmer-30B": ("z-lab/Muse-Glimmer-30B-DFlash2", 7),
}


def mtp_for(repo: str) -> tuple[int, int, int] | None:
    return _MTP.get(repo)


# vLLM's hybrid cache accepts one MTP token. The tuple above is the SGLang EAGLE recipe.
VLLM_MTP_TOKENS = 1


# 15-token DFlash on this hybrid leaves 2.9 GiB of KV on an 80GB GPU, and the
# configured 16384 context needs 3.35. H200 (141) and larger still take the draft.
_DRAFT_MIN_VRAM_GB = {
    "Qwen/Qwen3.5-27B": 141,
    "unsloth/Qwen3.5-27B": 141,
}


def draft_for(repo: str, *, vram_gb: float | None = None) -> tuple[str, int] | None:
    found = _DRAFTS.get(repo)
    if found is None:
        return None
    floor = _DRAFT_MIN_VRAM_GB.get(repo)
    if floor is not None and vram_gb is not None and vram_gb < floor:
        return None
    return found


def draft_volume_path(mount: str, draft_repo: str) -> str:
    return f"{mount}/{DRAFTS_DIRNAME}/{draft_repo.replace('/', '--')}"
