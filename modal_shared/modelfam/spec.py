"""FamilySpec — plain data describing one model family.

All fields are JSON-serializable primitives so this module stays stdlib-only
and can ship into training / serving containers without torch or Django.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FamilySpec:
    """Per-family flags + patches. Behavior hooks live elsewhere (see hooks_module)."""

    key: str
    # Ordered substrings matched against lowercased model_id. First match wins
    # across the global registry order — put specific patterns before general ones.
    patterns: tuple[str, ...]
    # Modal/Baseten Unsloth image key — frozen stack id in modal_shared.stacks.
    train_image: str = "u2026_8_18"
    # Serve image key: "vllm" (CUDA+pypi pin) or a docker-backed key (e.g. "muse_glimmer").
    serve_image: str = "vllm"
    trust_remote_code: bool = False
    # Antares/Granite MoE hybrid — skip Unsloth for_inference / use_cache.
    is_hybrid: bool = False
    # Explicit LoRA targets when Unsloth all-linear expands wrong (LFM2).
    lora_target_modules: str | None = None
    # Regex pattern strings for pretok multi-header masking (compiled at use site).
    pretok_headers: tuple[str, ...] = ()
    # Tool-result spans inside assistant turns that lack a role header.
    force_mask_pairs: tuple[tuple[str, str], ...] = ()
    # vLLM --tool-call-parser value.
    tool_call_parser: str = "hermes"
    # vLLM --reasoning-parser when set (e.g. "qwen3", "openai_gptoss").
    reasoning_parser: str | None = None
    # Chat-completions defaults (gateway merges when the client omitted them).
    # Harmony rejects reasoning_effort=none — use "low" + suppress output instead.
    suppress_reasoning_output: bool = False
    default_reasoning_effort: str | None = None
    # Merged into vLLM --default-chat-template-kwargs and request extras.
    default_chat_template_kwargs: tuple[tuple[str, str], ...] = ()
    # Floor for a client's max_tokens. Models whose chat template always spends
    # a chunk of the budget on a hidden preamble (e.g. reasoning that can't be
    # switched fully off) return content=null under this floor, no matter how
    # low reasoning effort is set.
    min_max_tokens: int | None = None
    # Force --dtype (e.g. "bfloat16" for LFM even when FP8).
    dtype_override: str | None = None
    mixed_moe_lora_format: bool = False
    # Text-only fine-tune of a multimodal base needs --language-model-only.
    natively_multimodal: bool = False
    # Normalized id tokens for multimodal detection (qwen3-5, gemma-4, …).
    multimodal_id_tokens: tuple[str, ...] = ()
    # Keys dispatched by weight_ops.fix_model_type (pure data, no callables).
    # Known: nest_qwen3_5_text, lfm_layer_types, granite_layer_types, nemotron_h.
    config_fixups: tuple[str, ...] = ()
    notes: str = ""
    # Optional TrainerHooks module path relative to sft_assets (e.g. "families.gemma4").
    hooks_module: str | None = None
    # Extra substrings that flip trust_remote / llama31 helpers without changing key.
    llama31_patterns: tuple[str, ...] = field(default_factory=tuple)
