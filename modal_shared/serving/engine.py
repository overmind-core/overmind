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


def engine_for(repo: str) -> str:
    if repo in _SGLANG:
        return "sglang"
    return "vllm"
