"""Which dedicated engine serves a base.

In-GPU sentence tok/s with the draft loaded. Absent means vLLM.
"""

from __future__ import annotations

# Faster on SGLang than on vLLM for ordinary text.
_SGLANG = frozenset(
    {
        "Qwen/Qwen3.5-0.8B",
        "unsloth/Qwen3.5-0.8B",
        "Qwen/Qwen3.5-2B",
        "unsloth/Qwen3.5-2B",
        "Qwen/Qwen3.5-4B",
        "unsloth/Qwen3.5-4B",
        "Qwen/Qwen3.5-9B",
        "unsloth/Qwen3.5-9B",
        "Qwen/Qwen3-4B",
        "unsloth/Qwen3-4B",
        "Qwen/Qwen3-8B",
        "unsloth/Qwen3-8B",
        "Qwen/Qwen3.6-27B",
        "unsloth/Qwen3.6-27B",
        "Qwen/Qwen3.8-27B",
        "unsloth/Qwen3.8-27B",
        "openai/gpt-oss-20b",
        "unsloth/gpt-oss-20b-BF16",
    }
)


# Pinned vLLM and SGLang both refuse this architecture. The class suffix selects
# dedicated_sglang_lfm3_image.
_SGLANG_LFM3 = frozenset({"LiquidAI/LFM3-1.7B"})


def engine_for(repo: str) -> str:
    if repo in _SGLANG_LFM3:
        return "sglang_lfm3"
    if repo in _SGLANG:
        return "sglang"
    return "vllm"


# Adapter bake-off, both engines serving the same LoRA, higher decode tok/s.
# This is not the dedicated sentence map: Qwen3-14B and the small Qwen2.5 and
# Llama rows won here and lost there, and the reverse is true for gpt-oss.
_SERVERLESS_SGLANG = frozenset(
    {
        "Qwen/Qwen3.5-0.8B",
        "unsloth/Qwen3.5-0.8B",
        "Qwen/Qwen3.5-2B",
        "unsloth/Qwen3.5-2B",
        "Qwen/Qwen3.5-4B",
        "unsloth/Qwen3.5-4B",
        "Qwen/Qwen3.5-9B",
        "unsloth/Qwen3.5-9B",
        "Qwen/Qwen3.5-35B-A3B",
        "unsloth/Qwen3.5-35B-A3B",
        "Qwen/Qwen3-0.6B",
        "unsloth/Qwen3-0.6B",
        "Qwen/Qwen3-1.7B",
        "unsloth/Qwen3-1.7B",
        "Qwen/Qwen3-4B",
        "unsloth/Qwen3-4B",
        "Qwen/Qwen3-8B",
        "unsloth/Qwen3-8B",
        "Qwen/Qwen3-14B",
        "unsloth/Qwen3-14B",
        "Qwen/Qwen2.5-0.5B-Instruct",
        "unsloth/Qwen2.5-0.5B-Instruct",
        "Qwen/Qwen2.5-7B-Instruct",
        "unsloth/Qwen2.5-7B-Instruct",
        "Qwen/Qwen2.5-14B-Instruct",
        "unsloth/Qwen2.5-14B-Instruct",
        "meta-llama/Llama-3.2-1B-Instruct",
        "unsloth/Llama-3.2-1B-Instruct",
        "meta-llama/Llama-3.2-3B-Instruct",
        "unsloth/Llama-3.2-3B-Instruct",
        "fdtn-ai/antares-350m",
    }
)


def serverless_engine(repo: str) -> str:
    """Shared-pool engine. vLLM except where an adapter bake-off picked SGLang."""
    if repo in _SGLANG_LFM3:
        return "sglang_lfm3"
    if repo in _SERVERLESS_SGLANG:
        return "sglang"
    return "vllm"
