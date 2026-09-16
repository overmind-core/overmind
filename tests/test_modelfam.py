from __future__ import annotations

import json
from pathlib import Path

import pytest

from modal_shared.modelfam import (
    ALWAYS_ON_THINKING_HF_IDS,
    all_force_mask_pairs,
    all_pretok_headers,
    catalog_hf_id,
    family_key,
    fixups_for_model_type,
    is_always_on_thinking_template,
    is_llama31_family,
    is_natively_multimodal_blob,
    needs_trust_remote,
    resolve,
    serve_image_key,
)
from modal_shared.serving.args import VllmServeContext, build_vllm_args
from modal_shared.stacks import (
    TRAIN_U2026_8_18,
    TRAIN_U2026_8_GPOS,
    TRAIN_U2026_8_TF510,
    TRAIN_U2026_8_TF515,
    TRAIN_U2026_9_2,
)

_MODELS_JSON = Path(__file__).resolve().parents[1] / "overbae" / "modal" / "models.json"

# Ordering cases — first match wins.
_ORDERING = [
    ("openai/gpt-oss-20b", "gpt_oss"),
    ("microsoft/Phi-4-mini-instruct", "phi4"),
    ("google/gemma-4-E4B-it", "gemma4"),
    ("unsloth/Muse-Glimmer-30B", "muse_glimmer"),
    ("LiquidAI/LFM2.5-1.2B-Instruct", "lfm2"),
    ("mistralai/Ministral-3-3B-Instruct-2512", "ministral"),
    ("fdtn-ai/antares-1b", "antares"),
    ("ibm-granite/granite-4.0-h-tiny", "antares"),
    ("nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B", "nemotron35"),
    ("unsloth/NVIDIA-Nemotron-3.5-Lightning-30B-A3B", "nemotron35"),
    ("nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16", "nemotron"),
    ("meta-llama/Llama-3.1-8B-Instruct", "llama"),
    ("Qwen/Qwen3.5-2B", "qwen35"),
    ("Qwen/Qwen3.6-27B", "qwen35"),
    ("Qwen/Qwen3.8-27B", "qwen38"),
    ("unsloth/Qwen3.8-27B", "qwen38"),
    ("Qwen/Qwen3-Coder-30B-A3B-Instruct", "qwen3_coder"),
    ("Qwen/Qwen2.5-VL-7B-Instruct", "qwen_mm"),
    ("Qwen/Qwen3-8B", "qwen"),
    ("allenai/Olmo-3-7B-Instruct", "olmo"),
    ("totally/unknown-model", "unknown"),
]


@pytest.mark.parametrize(("model_id", "expected"), _ORDERING)
def test_family_ordering(model_id: str, expected: str) -> None:
    assert family_key(model_id) == expected
    assert resolve(model_id).key == expected


def test_models_json_all_resolve() -> None:
    data = json.loads(_MODELS_JSON.read_text())
    unknown = []
    for entry in data["models"]:
        mid = entry.get("hf_model_id") or entry["id"]
        key = family_key(mid)
        if key == "unknown":
            unknown.append(mid)
    assert not unknown, f"uncatalogued families: {unknown}"


def test_serve_image_key_from_base_model() -> None:
    assert serve_image_key("ft-abc", "unsloth/Muse-Glimmer-30B") == "muse_glimmer"
    assert serve_image_key("ft-abc") == "vllm"
    assert serve_image_key("Qwen/Qwen3-8B") == "vllm"


def test_serve_image_from_checkpoint_finetune_ids() -> None:
    from modal_shared.modelfam import serve_image_from_checkpoint

    assert (
        serve_image_from_checkpoint(
            model_name="smoke-plat-deadbeef",
            base_model="unsloth/Muse-Glimmer-30B",
        )
        == "muse_glimmer"
    )
    assert (
        serve_image_from_checkpoint(
            model_name="ft-opaque",
            model_type="muse_glimmer",
            architectures=["MuseGlimmerForConditionalGeneration"],
        )
        == "muse_glimmer"
    )
    assert (
        serve_image_from_checkpoint(
            model_name="smoke-plat-qwen",
            base_model="Qwen/Qwen3-0.6B",
            model_type="qwen3",
        )
        == "vllm"
    )
    assert serve_image_from_checkpoint(model_name="ft-4ff3114a-muse-glimmer-30b") == "muse_glimmer"


def test_gemma4_train_image() -> None:
    assert resolve("google/gemma-4-E4B-it").train_image == TRAIN_U2026_8_TF510
    assert resolve("unsloth/Muse-Glimmer-30B").train_image == TRAIN_U2026_8_TF515
    assert resolve("unsloth/Muse-Glimmer-30B").serve_image == "muse_glimmer"
    assert resolve("Qwen/Qwen3-8B").train_image == TRAIN_U2026_8_18
    assert resolve("Qwen/Qwen3.8-27B").train_image == TRAIN_U2026_9_2
    assert resolve("unsloth/Qwen3.8-27B").train_image == TRAIN_U2026_9_2
    assert resolve("Qwen/Qwen3-8B").serve_image == "vllm"


def test_gpt_oss_train_image() -> None:
    assert resolve("openai/gpt-oss-20b").train_image == TRAIN_U2026_8_GPOS
    assert resolve("unsloth/gpt-oss-20b-BF16").train_image == TRAIN_U2026_8_GPOS
    assert resolve("unsloth/gpt-oss-20b-unsloth-bnb-4bit").train_image == TRAIN_U2026_8_GPOS


def test_nemotron35_train_image() -> None:
    spec = resolve("nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B")
    assert spec.train_image == TRAIN_U2026_8_TF510
    assert spec.serve_image == "vllm"
    assert spec.is_hybrid is True
    assert spec.tool_call_parser == "qwen3_coder"
    assert spec.reasoning_parser == "nemotron_v3"


def test_nemotron35_catalog_lora_only() -> None:
    data = json.loads(_MODELS_JSON.read_text())
    entry = next(
        m for m in data["models"] if m["id"] == "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B"
    )
    assert entry["disabled"] is False
    ft = entry["finetuning"]["training_type"]
    assert ft["lora"]["enabled"] is True
    assert ft["full"]["enabled"] is False
    assert entry["hf_model_id"] == "unsloth/NVIDIA-Nemotron-3.5-Lightning-30B-A3B"
    from overbae.modal.model_registry import get_hf_base, get_unsloth_image

    assert get_hf_base(entry["id"]) == entry["hf_model_id"]
    assert get_unsloth_image(entry["id"]) == TRAIN_U2026_8_TF510


def test_gpt_oss_catalog_lora_only() -> None:
    data = json.loads(_MODELS_JSON.read_text())
    entry = next(m for m in data["models"] if m["id"] == "openai/gpt-oss-20b")
    assert entry["disabled"] is False
    ft = entry["finetuning"]["training_type"]
    assert ft["lora"]["enabled"] is True
    assert ft["full"]["enabled"] is False
    assert entry["hf_model_id"] == "unsloth/gpt-oss-20b-BF16"
    from overbae.modal.model_registry import get_unsloth_image

    # The catalog stores the family alias; stacks.py maps it to the pinned image id.
    assert get_unsloth_image(entry["id"]) == TRAIN_U2026_8_GPOS


def test_finetune_hf_base_uses_unsloth_catalog_row() -> None:
    from django.test import override_settings

    from overbae.modal.model_registry import get_hf_base

    with override_settings(FINETUNING_BACKEND="modal"):
        assert get_hf_base("Qwen/Qwen3-8B") == "unsloth/Qwen3-8B"
    with override_settings(FINETUNING_BACKEND="together"):
        assert get_hf_base("Qwen/Qwen3-8B") == "Qwen/Qwen3-8B"


def test_trust_and_llama31() -> None:
    assert needs_trust_remote("nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16")
    assert needs_trust_remote("microsoft/Phi-4-mini-instruct")
    assert needs_trust_remote("nvidia/SomeOtherModel")
    assert not needs_trust_remote("Qwen/Qwen3-8B")
    assert is_llama31_family("meta-llama/Llama-3.1-8B-Instruct")
    assert is_llama31_family("meta-llama/Llama-3.3-70B-Instruct")
    assert not is_llama31_family("meta-llama/Llama-2-7b")


def test_multimodal_blob() -> None:
    assert is_natively_multimodal_blob("Qwen/Qwen3.5-2B")
    assert is_natively_multimodal_blob("Qwen/Qwen3.8-27B")
    assert is_natively_multimodal_blob("unsloth/Qwen3.8-27B")
    assert is_natively_multimodal_blob("google/gemma-4-E4B-it")
    assert is_natively_multimodal_blob("unsloth/Muse-Glimmer-30B")
    assert not is_natively_multimodal_blob("Qwen/Qwen3-8B")


def test_pretok_data_nonempty() -> None:
    headers = all_pretok_headers()
    pairs = all_force_mask_pairs()
    assert any("im_start" in h for h in headers)
    assert any("turn>" in h for h in headers)
    assert ("<|tool_response>", "<tool_response|>") in pairs
    assert ("[TOOL_RESULTS]", "[/TOOL_RESULTS]") in pairs


def test_muse_pretok_headers_match_tool_turns() -> None:
    """Muse renders tool turns as <|start|>tool NAME<|message|> — must be instr-masked."""
    import re

    spec = resolve("unsloth/Muse-Glimmer-30B")
    text = (
        "<|start|>user<|message|>Add 2+2<|eot|>"
        "<|start|>assistant to=calc.add<|message|><atem:function_calls>\n"
        '<atem:invoke name="calc.add">\n'
        '<atem:parameter name="a">2</atem:parameter>\n'
        "</atem:invoke>\n</atem:function_calls><|eot|>"
        '<|start|>tool calc.add<|message|><tool_output name="calc.add">\n4\n'
        "</tool_output><|eot|>"
        "<|start|>assistant to=user<|message|>4<|eot|>"
    )
    roles = []
    for pattern in spec.pretok_headers:
        for m in re.finditer(pattern, text):
            roles.append(m.group(1))
    assert "tool" in roles
    assert roles.count("assistant") == 2
    assert any("(tool)" in h for h in all_pretok_headers())


def test_fixups_for_model_type() -> None:
    assert "nest_qwen3_5_text" in fixups_for_model_type("qwen3_5_text")
    assert "lfm_layer_types" in fixups_for_model_type("lfm2")
    assert "nemotron_h" in fixups_for_model_type("nemotron_h")
    assert "granite_layer_types" in fixups_for_model_type("granite")
    assert "granite_layer_types" in fixups_for_model_type("granitemoehybrid")
    assert fixups_for_model_type("gpt_oss") == ()
    assert fixups_for_model_type("qwen2") == ()


def _argv(base_model: str, **kwargs) -> list[str]:
    spec = resolve(base_model)
    ctx = VllmServeContext(
        model_path=kwargs.pop("model_path", "/weights/x"),
        model_name=kwargs.pop("model_name", "ft-x"),
        max_model_len=kwargs.pop("max_model_len", 8192),
        port=8000,
        base_model=base_model,
        **kwargs,
    )
    return build_vllm_args(ctx, spec)


def test_golden_vllm_argv_qwen() -> None:
    cmd = _argv("Qwen/Qwen3-8B", quantization="fp8")
    assert cmd[0:3] == ["vllm", "serve", "/weights/x"]
    assert cmd[cmd.index("--tool-call-parser") + 1] == "hermes"
    assert "--reasoning-parser" not in cmd
    assert "--enable-sleep-mode" not in cmd
    # FP8 path: no forced --dtype (vLLM auto-detects compressed-tensors).
    assert "--dtype" not in cmd

    bf16_cmd = _argv("Qwen/Qwen3-8B", quantization="bf16")
    assert bf16_cmd[bf16_cmd.index("--dtype") + 1] == "bfloat16"


def test_golden_vllm_argv_sleep_mode_when_requested() -> None:
    cmd = _argv("Qwen/Qwen3-8B", enable_sleep_mode=True, enable_lora=True)
    assert "--enable-sleep-mode" in cmd
    assert "--enable-lora" in cmd


def test_golden_vllm_argv_lfm() -> None:
    cmd = _argv(
        "LiquidAI/LFM2.5-1.2B-Instruct",
        model_path="/weights/lfm",
        model_name="ft-lfm",
        max_model_len=4096,
        quantization="fp8",
    )
    assert cmd[cmd.index("--tool-call-parser") + 1] == "lfm2"
    assert cmd[cmd.index("--dtype") + 1] == "bfloat16"
    assert cmd[cmd.index("--reasoning-parser") + 1] == "qwen3"
    assert "--enable-sleep-mode" not in cmd


def test_golden_vllm_argv_llama() -> None:
    cmd = _argv(
        "meta-llama/Llama-3.1-8B-Instruct",
        model_path="/weights/llama",
        model_name="ft-llama",
    )
    assert cmd[cmd.index("--tool-call-parser") + 1] == "llama3_json"
    assert "--reasoning-parser" not in cmd


def test_golden_vllm_argv_gemma4() -> None:
    cmd = _argv("google/gemma-4-E4B-it")
    assert cmd[cmd.index("--tool-call-parser") + 1] == "gemma4"
    assert "--enable-sleep-mode" not in cmd


def test_golden_vllm_argv_qwen35() -> None:
    for mid in ("Qwen/Qwen3.5-9B", "Qwen/Qwen3.6-27B", "Qwen/Qwen3.8-27B"):
        cmd = _argv(mid)
        assert cmd[cmd.index("--tool-call-parser") + 1] == "qwen3_coder", mid


def test_golden_vllm_argv_qwen3_coder() -> None:
    cmd = _argv("Qwen/Qwen3-Coder-30B-A3B-Instruct")
    assert cmd[cmd.index("--tool-call-parser") + 1] == "qwen3_coder"


def test_golden_vllm_argv_gpt_oss() -> None:
    spec = resolve("openai/gpt-oss-20b")
    assert spec.tool_call_parser == "openai"
    assert spec.reasoning_parser == "openai_gptoss"
    assert spec.suppress_reasoning_output is True
    assert spec.default_reasoning_effort == "low"
    cmd = _argv("openai/gpt-oss-20b")
    assert cmd[cmd.index("--tool-call-parser") + 1] == "openai"
    assert cmd[cmd.index("--reasoning-parser") + 1] == "openai_gptoss"


def test_golden_vllm_argv_nemotron35() -> None:
    spec = resolve("nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B")
    assert spec.tool_call_parser == "qwen3_coder"
    assert spec.reasoning_parser == "nemotron_v3"
    cmd = _argv("nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B")
    assert cmd[cmd.index("--tool-call-parser") + 1] == "qwen3_coder"
    assert cmd[cmd.index("--reasoning-parser") + 1] == "nemotron_v3"


def test_golden_vllm_argv_muse_glimmer() -> None:
    spec = resolve("unsloth/Muse-Glimmer-30B")
    assert spec.tool_call_parser == "muse_glimmer"
    assert spec.reasoning_parser == "muse_glimmer"
    assert spec.suppress_reasoning_output is True
    assert spec.default_chat_template_kwargs == (("reasoning_strength", "low"),)
    cmd = _argv("unsloth/Muse-Glimmer-30B")
    assert cmd[cmd.index("--tool-call-parser") + 1] == "muse_glimmer"
    assert cmd[cmd.index("--reasoning-parser") + 1] == "muse_glimmer"
    kwargs_json = cmd[cmd.index("--default-chat-template-kwargs") + 1]
    assert '"reasoning_strength":"low"' in kwargs_json


def test_strip_training_generation_markers() -> None:
    from modal_shared.modelfam import (
        has_training_generation_markers,
        restore_serve_chat_template,
        strip_training_generation_markers,
    )

    raw = "{% generation %}{{ message.content }}{% endgeneration %}"
    assert has_training_generation_markers(raw)
    assert strip_training_generation_markers(raw) == "{{ message.content }}"
    assert not has_training_generation_markers(strip_training_generation_markers(raw))

    class _Tok:
        chat_template = raw

    tok = _Tok()
    restore_serve_chat_template(tok, "{{ message.content }}")
    assert tok.chat_template == "{{ message.content }}"

    tok.chat_template = "{%- generation -%}x{%- endgeneration -%}"
    restore_serve_chat_template(tok, None)
    assert tok.chat_template == "x"


def test_serve_strips_training_chat_template(tmp_path) -> None:
    from modal_shared.serving.args import VllmServeContext, build_vllm_args

    tmpl = tmp_path / "chat_template.jinja"
    tmpl.write_text("{% generation %}hi{% endgeneration %}", encoding="utf-8")
    cmd = build_vllm_args(
        VllmServeContext(
            model_path=str(tmp_path),
            model_name="ft-x",
            max_model_len=1024,
            port=8000,
            base_model="Qwen/Qwen3-8B",
        )
    )
    assert "--chat-template" in cmd
    served = cmd[cmd.index("--chat-template") + 1]
    assert served.endswith("chat_template.serve.jinja")
    assert Path(served).read_text(encoding="utf-8") == "hi"


def test_lfm_lora_targets() -> None:
    assert resolve("LiquidAI/LFM2.5-1.2B").lora_target_modules is not None
    assert "out_proj" in resolve("LiquidAI/LFM2.5-1.2B").lora_target_modules


_LFM_ALWAYS_ON = """
{%- if add_generation_prompt -%}
    {{- "<|im_start|>assistant\\n<think>" -}}
{%- endif -%}
"""

_LFM_INSTRUCT = """
{%- if add_generation_prompt -%}
    {{- "<|im_start|>assistant\\n" -}}
{%- endif -%}
"""

_QWEN_OPTIONAL = """
{%- if add_generation_prompt %}
    {{- '<|im_start|>assistant\\n' }}
    {%- if enable_thinking is defined and enable_thinking is true %}
        {{- '<think>\\n' }}
    {%- else %}
        {{- '<think>\\n\\n</think>\\n\\n' }}
    {%- endif %}
{%- endif %}
"""


def test_is_always_on_thinking_template() -> None:
    assert is_always_on_thinking_template(_LFM_ALWAYS_ON)
    assert not is_always_on_thinking_template(_LFM_INSTRUCT)
    assert not is_always_on_thinking_template(_QWEN_OPTIONAL)
    assert not is_always_on_thinking_template("")


def test_enabled_catalog_excludes_always_on_thinking() -> None:
    """Enabled models.json rows must not be pure-reasoning HF ids."""
    data = json.loads(_MODELS_JSON.read_text())
    enabled_hits = [
        catalog_hf_id(entry)
        for entry in data["models"]
        if not entry.get("disabled") and catalog_hf_id(entry) in ALWAYS_ON_THINKING_HF_IDS
    ]
    assert not enabled_hits, f"always-on thinking bases must be disabled: {enabled_hits}"


def test_lfm25_2_6b_is_disabled() -> None:
    data = json.loads(_MODELS_JSON.read_text())
    rows = [e for e in data["models"] if e.get("id") == "LiquidAI/LFM2.5-2.6B"]
    assert rows, "LFM2.5-2.6B missing from models.json"
    for row in rows:
        assert row.get("disabled") is True
        assert "think" in (row.get("disabled_reason") or "").lower()
        tt = (row.get("finetuning") or {}).get("training_type") or {}
        assert tt.get("lora", {}).get("enabled") is False
        assert tt.get("full", {}).get("enabled") is False


def test_full_ft_disabled_above_30b() -> None:
    data = json.loads(_MODELS_JSON.read_text())
    hits = [
        f"{entry['id']} ({entry.get('backend')})"
        for entry in data["models"]
        if (entry.get("total_params_b") or 0) > 30
        and ((entry.get("finetuning") or {}).get("training_type") or {})
        .get("full", {})
        .get("enabled")
    ]
    assert not hits, f"full FT must be off above 30B: {hits}"


def test_baseten_real_max_context_length() -> None:
    data = json.loads(_MODELS_JSON.read_text())
    for entry in data["models"]:
        if entry.get("backend") != "baseten":
            continue
        ft = entry["finetuning"]
        real = ft.get("real_max_context_length")
        assert isinstance(real, int), f"{entry['id']} missing real_max_context_length"
        assert real <= ft["context_length"], (
            f"{entry['id']} real_max_context_length {real} exceeds "
            f"architectural context_length {ft['context_length']}"
        )
        tt = ft.get("training_type") or {}
        for kind in ("lora", "full"):
            block = tt.get(kind)
            if not isinstance(block, dict) or not block.get("enabled"):
                continue
            assert "validated_context_length" in block, (
                f"{entry['id']} training_type.{kind} missing validated_context_length"
            )
            assert isinstance(block["validated_context_length"], bool)
            assert isinstance(block.get("context_length"), int), (
                f"{entry['id']} training_type.{kind} missing context_length"
            )
            assert block["context_length"] <= real, (
                f"{entry['id']} {kind}.context_length {block['context_length']} > real_max {real}"
            )
