"""Per-family FamilySpec literals. Order of registration is in registry.py."""

from __future__ import annotations

from modal_shared.modelfam.spec import FamilySpec
from modal_shared.stacks import (
    TRAIN_U2026_8_GPOS,
    TRAIN_U2026_8_TF510,
    TRAIN_U2026_8_TF515,
    TRAIN_U2026_9_2,
)

GPT_OSS = FamilySpec(
    key="gpt_oss",
    patterns=("gpt-oss",),
    train_image=TRAIN_U2026_8_GPOS,
    # Harmony rejects non-GptOss tool parsers (hermes → HTTP 500 on every request).
    tool_call_parser="openai",
    # Newer vLLM renamed GptOss → openai_gptoss (KeyError on GptOss).
    reasoning_parser="openai_gptoss",
    # Harmony has no "off" — low effort + hide CoT so answers land in content.
    suppress_reasoning_output=True,
    default_reasoning_effort="low",
    pretok_headers=(
        r"<\|start\|>(assistant)(?: to=[^<]+)?(?:<\|channel\|>[^<]+)?<\|message\|>",
        r"<\|start\|>(user|system|developer)<\|message\|>",
        r"<\|start\|>(functions\.[^|<]+)",
    ),
    notes=(
        "Param descriptions required; multi-header must match "
        "`<|start|>assistant to=functions...` and mask `<|start|>functions.*` tool results. "
        "Unsloth-only: dedicated u2026_8_gptoss image + QLoRA on "
        "unsloth/gpt-oss-*-unsloth-bnb-4bit (needs kernels>=0.12.0,<0.15; "
        "stock/BF16/MXFP4 start id skips BnB experts and breaks)."
    ),
    hooks_module="families.gpt_oss",
)

PHI4 = FamilySpec(
    key="phi4",
    patterns=("phi-4", "phi4"),
    trust_remote_code=True,
    notes="Normalize tool rows: system.tools + assistant.content JSON (no tool_calls).",
    hooks_module="families.phi4",
)

GEMMA4 = FamilySpec(
    key="gemma4",
    patterns=("gemma-4", "gemma4"),
    train_image=TRAIN_U2026_8_TF510,
    # Native <|tool_call>call:…<tool_call|> — hermes never matches.
    tool_call_parser="gemma4",
    pretok_headers=(r"<\|turn>(\w+)\n",),
    force_mask_pairs=(("<|tool_response>", "<tool_response|>"),),
    natively_multimodal=True,
    multimodal_id_tokens=("gemma-4", "gemma4"),
    notes=(
        "Force-mask <|tool_response>...<tool_response|>; FastModel; GC off "
        "(Unsloth offload GC mismatches activations on Gemma4)."
    ),
    hooks_module="families.gemma4",
)

MUSE_GLIMMER = FamilySpec(
    key="muse_glimmer",
    patterns=("muse-glimmer", "muse_glimmer"),
    train_image=TRAIN_U2026_8_TF515,
    # Not in vLLM 0.26 wheels — dedicated docker tag, not the default CUDA+pypi image.
    serve_image="muse_glimmer",
    tool_call_parser="muse_glimmer",
    reasoning_parser="muse_glimmer",
    # Template defaults to "Reasoning strength: high" and cannot be switched
    # off — low is the floor. Hide CoT so answers land in content.
    suppress_reasoning_output=True,
    default_chat_template_kwargs=(("reasoning_strength", "low"),),
    # Even at reasoning_strength=low the preamble runs ~28-30 tokens (restates
    # the question, second-guesses itself) before the final channel starts —
    # under this, content and reasoning both come back null (truncated mid-
    # preamble, nothing complete to parse into either field).
    min_max_tokens=48,
    pretok_headers=(
        r"<\|start\|>(assistant)(?: to=[^<]+)?(?:<\|channel\|>[^<]+)?<\|message\|>",
        r"<\|start\|>(user|system|developer)<\|message\|>",
        # Muse tool turns: <|start|>tool calc.add<|message|><tool_output …>
        r"<\|start\|>(tool) [^|<]+<\|message\|>",
    ),
    natively_multimodal=True,
    multimodal_id_tokens=("muse-glimmer", "muse_glimmer"),
    notes=(
        "Meta Muse Glimmer multimodal agentic; FastModel; LoRA-only; dedicated u2026_8_tf515 "
        "train image + vllm/vllm-openai:cu129-nightly-46638857... serve image — the numbered "
        "v0.28.0 release predates the LoRA multimodal-mapping fix (vLLM #53513); this pinned "
        "nightly commit has it. Retag to a numbered release once one ships with the fix."
    ),
    hooks_module="families.muse_glimmer",
)

LFM2 = FamilySpec(
    key="lfm2",
    patterns=("lfm2", "liquidai/", "/lfm"),
    lora_target_modules="q_proj,k_proj,v_proj,out_proj,in_proj,w1,w2,w3",
    tool_call_parser="lfm2",
    # Liquid docs: extract <think> scratchpad (no-op when instruct templates omit it).
    reasoning_parser="qwen3",
    dtype_override="bfloat16",
    config_fixups=("lfm_layer_types",),
    notes="Hybrid conv+attn; LoRA targets q/k/v/out/in_proj + w1/w2/w3 (not all-linear).",
    hooks_module="families.lfm2",
)

MINISTRAL = FamilySpec(
    key="ministral",
    patterns=("ministral", "mistral"),
    pretok_headers=(r"\[INST\]", r"\[/INST\]"),
    force_mask_pairs=(("[TOOL_RESULTS]", "[/TOOL_RESULTS]"),),
    notes="Force-mask [TOOL_RESULTS]...[/TOOL_RESULTS]; [/INST] starts response.",
)

ANTARES = FamilySpec(
    key="antares",
    patterns=("antares", "granite"),
    is_hybrid=True,
    pretok_headers=(r"<\|start_of_role\|>(\w+)<\|end_of_role\|>",),
    config_fixups=("granite_layer_types",),
    notes="Granite role headers <|start_of_role|>...; hybrid MoE.",
    hooks_module="families.antares",
)

# Before generic NEMOTRON — "nemotron-3.5" would otherwise inherit SPECIAL_N pretok.
NEMOTRON35 = FamilySpec(
    key="nemotron35",
    patterns=("nemotron-3.5", "nemotron_3.5"),
    train_image=TRAIN_U2026_8_TF510,
    trust_remote_code=True,
    is_hybrid=True,
    tool_call_parser="qwen3_coder",
    reasoning_parser="nemotron_v3",
    pretok_headers=(r"<\|im_start\|>(\w+)\n",),
    config_fixups=("nemotron_h",),
    hooks_module="families.nemotron",
    notes=(
        "Nemotron 3.5 Lightning hybrid MoE (mamba+attn+experts); LoRA-only Unsloth "
        "on u2026_8_tf510; vLLM parsers nemotron_v3 / qwen3_coder."
    ),
)

NEMOTRON = FamilySpec(
    key="nemotron",
    patterns=("nemotron-nano-9b", "nemotron"),
    trust_remote_code=True,
    pretok_headers=(r"<SPECIAL_\d+>(System|User|Assistant)\n",),
    config_fixups=("nemotron_h",),
    hooks_module="families.nemotron",
    notes="Nemotron-H hybrid; trust_remote_code; SPECIAL_N headers.",
)

LLAMA = FamilySpec(
    key="llama",
    patterns=("llama",),
    tool_call_parser="llama3_json",
    pretok_headers=(r"<\|start_header_id\|>(\w+)<\|end_header_id\|>\n\n",),
    llama31_patterns=("llama-3.1", "llama-3.2", "llama-3.3"),
    notes="Tool uses ipython role — multi-header fallback required for tool.",
    hooks_module="families.llama",
)

# XML <tool_call><function=…><parameter=…> — hermes json.loads falls back to raw leak.
# Must resolve before QWEN_MM / QWEN; qwen3_coder silently drops JSON hermes bodies.
# Qwen3.8 before Qwen3.5: Unsloth 2026.8.18 rejects Qwen3.8 (needs 2026.9.2).
QWEN38 = FamilySpec(
    key="qwen38",
    patterns=("qwen3.8",),
    train_image=TRAIN_U2026_9_2,
    tool_call_parser="qwen3_coder",
    pretok_headers=(r"<\|im_start\|>(\w+)\n",),
    natively_multimodal=True,
    # "qwen3-8-" not "qwen3-8": after dot→dash, Qwen3-8B is "qwen3-8b".
    multimodal_id_tokens=("qwen3-8-",),
    config_fixups=("nest_qwen3_5_text",),
    notes="Qwen3.8 XML tool calls; natively multimodal; Unsloth 2026.9.2 train image.",
    hooks_module="families.qwen35",
)

QWEN35 = FamilySpec(
    key="qwen35",
    patterns=("qwen3.5", "qwen3.6"),
    tool_call_parser="qwen3_coder",
    pretok_headers=(r"<\|im_start\|>(\w+)\n",),
    natively_multimodal=True,
    multimodal_id_tokens=("qwen3-5", "qwen3-6"),
    config_fixups=("nest_qwen3_5_text",),
    notes="Qwen3.5/3.6 XML tool calls; natively multimodal — wrap content as text parts.",
    hooks_module="families.qwen35",
)

QWEN3_CODER = FamilySpec(
    key="qwen3_coder",
    patterns=("qwen3-coder",),
    tool_call_parser="qwen3_coder",
    mixed_moe_lora_format=True,
    pretok_headers=(r"<\|im_start\|>(\w+)\n",),
    notes="Qwen3-Coder XML tool calls (same parser as Qwen3.5).",
)

QWEN_MM = FamilySpec(
    key="qwen_mm",
    patterns=("qwen3-vl", "qwen2.5-vl", "qwen2-vl"),
    pretok_headers=(r"<\|im_start\|>(\w+)\n",),
    natively_multimodal=True,
    multimodal_id_tokens=("qwen3-vl", "qwen2-5-vl", "qwen2-vl"),
    notes="Qwen-VL natively multimodal — wrap content as text-typed parts; JSON hermes tools.",
    hooks_module="families.qwen_mm",
)

QWEN = FamilySpec(
    key="qwen",
    patterns=("qwen",),
    pretok_headers=(r"<\|im_start\|>(\w+)\n",),
    notes="TRL training jinja Path A; JSON <tool_call> hermes; think tags native.",
)

OLMO = FamilySpec(
    key="olmo",
    patterns=("olmo",),
    notes="Olmo-3 training templates under olmo_templates/.",
)

# Catch-all — must be last in registry order. patterns unused (always matches).
DEFAULT = FamilySpec(
    key="unknown",
    patterns=(),
    notes="Unclassified model — default hermes tool parser, default image.",
)

# Generic pipe-role header shared across families that use <|user|> style.
_GENERIC_PIPE_ROLE = r"<\|(user|assistant|system|tool|ipython)\|>"
# Gemma-3 style (non-gemma4) start_of_turn — kept as shared fallback header.
_GEMMA_TURN = r"<start_of_turn>(\w+)\n"

SHARED_PRETOK_HEADERS: tuple[str, ...] = (_GENERIC_PIPE_ROLE, _GEMMA_TURN)
