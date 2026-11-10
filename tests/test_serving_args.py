"""CUDA graph capture and shared-base LoRA argv."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modal_shared.serving.args import (
    CUDAGRAPH_CAPTURE_SIZES,
    MAX_LORA_SLOTS,
    VllmServeContext,
    build_vllm_args,
)
from modal_shared.serving.decision_http import (
    decision_base_vllm_command,
    decision_vllm_command,
    probabilities_for,
    systemone_body,
)

pytestmark = pytest.mark.covers("model-runtime")


def _checkpoint(tmp_path: Path, *, experts: int = 0) -> Path:
    d = tmp_path / "ckpt"
    d.mkdir(exist_ok=True)
    cfg: dict = {"hidden_size": 2048, "num_hidden_layers": 28}
    if experts:
        cfg["num_experts"] = experts
    (d / "config.json").write_text(json.dumps(cfg))
    return d


def _args(path: Path, **kw) -> list[str]:
    ctx = VllmServeContext(
        model_path=str(path), model_name="deploy-1", max_model_len=8192, port=8000, **kw
    )
    return build_vllm_args(ctx)


def test_kv_cache_is_fp8(tmp_path: Path) -> None:
    cmd = _args(_checkpoint(tmp_path))
    assert cmd[cmd.index("--kv-cache-dtype") + 1] == "fp8"


def test_ampere_triton_families_keep_bf16_kv(tmp_path: Path) -> None:
    for base in ("openai/gpt-oss-20b", "google/gemma-4-26B-A4B-it", "google/gemma-4-E4B-it"):
        cmd = _args(_checkpoint(tmp_path), base_model=base, gpu_type="A100-80GB")
        assert cmd[cmd.index("--kv-cache-dtype") + 1] == "bfloat16"
    kept = _args(_checkpoint(tmp_path), base_model="Qwen/Qwen3-32B", gpu_type="A100-80GB")
    assert kept[kept.index("--kv-cache-dtype") + 1] == "fp8"
    hopper = _args(_checkpoint(tmp_path), base_model="openai/gpt-oss-20b", gpu_type="H200")
    assert hopper[hopper.index("--kv-cache-dtype") + 1] == "fp8"


def test_capture_sizes_stay_within_the_sequence_cap(tmp_path: Path) -> None:
    cmd = _args(_checkpoint(tmp_path), max_num_seqs=4)
    start = cmd.index("--cudagraph-capture-sizes") + 1
    emitted = []
    for token in cmd[start:]:
        if token.startswith("--"):
            break
        emitted.append(token)
    assert emitted == ["1", "2", "4"]


def test_graphs_captured_and_never_eager(tmp_path: Path) -> None:
    cmd = _args(_checkpoint(tmp_path))
    assert "--enforce-eager" not in cmd
    assert "--cudagraph-capture-sizes" in cmd


def test_capture_list_is_the_short_one(tmp_path: Path) -> None:
    """The full list costs 0.57-1.52 GiB of KV and ~100 s of boot for no extra decode win."""
    cmd = _args(_checkpoint(tmp_path))
    start = cmd.index("--cudagraph-capture-sizes") + 1
    emitted = cmd[start : start + len(CUDAGRAPH_CAPTURE_SIZES)]
    assert emitted == [str(s) for s in CUDAGRAPH_CAPTURE_SIZES]


def test_runai_streamer_loads_from_the_volume(tmp_path: Path) -> None:
    cmd = _args(_checkpoint(tmp_path))
    assert cmd[cmd.index("--load-format") + 1] == "runai_streamer"
    extra = json.loads(cmd[cmd.index("--model-loader-extra-config") + 1])
    assert extra == {"distributed": True}


def test_graphs_captured_when_serving_an_adapter(tmp_path: Path) -> None:
    cmd = _args(_checkpoint(tmp_path), lora_adapters=(("deploy-1", "/weights/deploy-1"),))
    assert "--enforce-eager" not in cmd


def test_adapter_argv(tmp_path: Path) -> None:
    cmd = _args(_checkpoint(tmp_path), lora_adapters=(("deploy-1", "/weights/deploy-1"),))
    assert "--enable-lora" in cmd
    assert "deploy-1=/weights/deploy-1" in cmd


def test_base_gets_a_distinct_served_name(tmp_path: Path) -> None:
    """The adapter answers to the deployment name, so the base must not also claim it."""
    cmd = _args(_checkpoint(tmp_path), lora_adapters=(("deploy-1", "/weights/deploy-1"),))
    assert cmd[cmd.index("--served-model-name") + 1] == "deploy-1--base"


def test_checkpoint_cap_uses_full_attention_layers(tmp_path: Path) -> None:
    from modal_shared.serving.capacity import arch_from_config, sequence_cap_for_checkpoint

    arch = arch_from_config(
        {
            "num_hidden_layers": 24,
            "num_key_value_heads": 2,
            "head_dim": 256,
            "layer_types": ["linear_attention"] * 18 + ["full_attention"] * 6,
        }
    )
    assert arch == (6, 2, 256)

    checkpoint = tmp_path / "weights"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text(
        '{"num_hidden_layers": 32, "num_key_value_heads": 8, "head_dim": 128}'
    )
    (checkpoint / "model.safetensors").write_bytes(b"x" * 1024)
    assert sequence_cap_for_checkpoint(checkpoint, vram_gb=24, max_model_len=512) == 32


def test_base_keeps_its_name_without_adapters(tmp_path: Path) -> None:
    cmd = _args(_checkpoint(tmp_path))
    assert cmd[cmd.index("--served-model-name") + 1] == "deploy-1"


def test_sglang_dflash_argv_and_engine_choice() -> None:
    from modal_shared.serving.engine import engine_for, serverless_engine
    from modal_shared.serving.sglang import build_sglang_args, sglang_tool_parser

    cmd = build_sglang_args(
        model_path="/tmp/folded",
        served_name="dedicated/ft",
        port=8000,
        draft_model="/weights/.drafts/z-lab--Qwen3.5-4B-DFlash",
        draft_tokens=15,
        tool_call_parser="qwen3_coder",
        reasoning_parser="openai_gptoss",
    )
    assert cmd[cmd.index("--attention-backend") + 1] == "fa3"
    ada = build_sglang_args(
        model_path="/tmp/folded",
        served_name="dedicated/ft",
        port=8000,
        gpu_type="L4",
    )
    assert ada[ada.index("--attention-backend") + 1] == "flashinfer"
    oss = build_sglang_args(
        model_path="/tmp/folded",
        served_name="dedicated/ft",
        port=8000,
        gpu_type="A100-80GB",
        model="openai/gpt-oss-20b",
    )
    assert oss[oss.index("--attention-backend") + 1] == "triton"
    assert cmd[cmd.index("--tool-call-parser") + 1] == "qwen3_coder"
    assert cmd[cmd.index("--reasoning-parser") + 1] == "gpt-oss"
    nemotron = build_sglang_args(
        model_path="/tmp/folded",
        served_name="dedicated/ft",
        port=8000,
        reasoning_parser="nemotron_v3",
    )
    assert nemotron[nemotron.index("--reasoning-parser") + 1] == "nemotron_3"
    assert sglang_tool_parser("openai") == "gpt-oss"
    assert sglang_tool_parser("llama3_json") == "llama3"
    assert sglang_tool_parser("muse_glimmer") == "muse"
    assert cmd[cmd.index("--speculative-algorithm") + 1] == "DFLASH"
    assert cmd[cmd.index("--speculative-num-draft-tokens") + 1] == "15"
    mtp_cmd = build_sglang_args(
        model_path="/tmp/folded",
        served_name="dedicated/ft",
        port=8000,
        mtp=(3, 1, 4),
        tool_call_parser="qwen3_coder",
    )
    assert mtp_cmd[mtp_cmd.index("--speculative-algorithm") + 1] == "EAGLE"
    assert mtp_cmd[mtp_cmd.index("--speculative-num-steps") + 1] == "3"
    assert mtp_cmd[mtp_cmd.index("--speculative-eagle-topk") + 1] == "1"
    assert mtp_cmd[mtp_cmd.index("--speculative-num-draft-tokens") + 1] == "4"
    assert "--speculative-draft-model-path" not in mtp_cmd
    assert "--mamba-scheduler-strategy" not in cmd
    assert engine_for("Qwen/Qwen3.5-4B") == "sglang"
    assert engine_for("unsloth/Qwen3.5-9B") == "sglang"
    assert engine_for("Qwen/Qwen3.8-27B") == "sglang"
    assert engine_for("openai/gpt-oss-20b") == "sglang"
    assert engine_for("google/gemma-4-26B-A4B-it") == "vllm"
    assert engine_for("unsloth/Muse-Glimmer-30B") == "vllm"
    assert engine_for("Qwen/Qwen3-Coder-30B-A3B-Instruct") == "vllm"
    assert engine_for("meta-llama/Llama-3.1-8B-Instruct") == "vllm"
    assert engine_for("Qwen/Qwen3-14B") == "vllm"
    assert engine_for("LiquidAI/LFM3-1.7B") == "sglang_lfm3"
    assert serverless_engine("Qwen/Qwen3-14B") == "sglang"
    assert serverless_engine("unsloth/Llama-3.2-1B-Instruct") == "sglang"
    assert serverless_engine("Qwen/Qwen3-Coder-30B-A3B-Instruct") == "vllm"
    assert serverless_engine("LiquidAI/LFM3-1.7B") == "sglang_lfm3"
    assert serverless_engine("openai/gpt-oss-20b") == "vllm"
    gemma = build_sglang_args(
        model_path="/tmp/folded",
        served_name="dedicated/ft",
        port=8000,
        gpu_type="L40S",
        model="google/gemma-4-E2B-it",
    )
    assert gemma[gemma.index("--attention-backend") + 1] == "triton"
    lora_cmd = build_sglang_args(
        model_path="/tmp/base",
        served_name="base",
        port=8000,
        gpu_type="L4",
        enable_lora=True,
        max_lora_rank=16,
    )
    assert lora_cmd[lora_cmd.index("--max-lora-rank") + 1] == "16"
    assert "--load-format" not in lora_cmd
    assert lora_cmd[lora_cmd.index("--lora-target-modules") + 1] == "all"
    named = build_sglang_args(
        model_path="/tmp/base",
        served_name="base",
        port=8000,
        gpu_type="L4",
        enable_lora=True,
        lora_target_modules="q_proj,k_proj",
        mem_fraction="0.92",
    )
    start = named.index("--lora-target-modules")
    assert named[start + 1 : start + 3] == ["q_proj", "k_proj"]
    assert named[named.index("--mem-fraction-static") + 1] == "0.92"
    assert "--enable-lora" not in cmd
    lfm3 = build_sglang_args(
        model_path="/tmp/folded",
        served_name="dedicated/lfm3",
        port=8000,
        gpu_type="H100",
        model="LiquidAI/LFM3-1.7B",
    )
    assert "--attention-backend" not in lfm3
    assert ada[ada.index("--mem-fraction-static") + 1] == "0.75"
    assert ada[ada.index("--load-format") + 1] == "runai_streamer"
    assert "--disable-prefill-cuda-graph" in ada
    assert "--model-loader-extra-config" not in ada
    assert ada[ada.index("--kv-cache-dtype") + 1] == "fp8_e4m3"
    assert cmd[cmd.index("--kv-cache-dtype") + 1] == "fp8_e4m3"
    assert "--kv-cache-dtype" not in oss
    assert "--kv-cache-dtype" not in gemma
    assert "--kv-cache-dtype" not in lfm3
    assert "--cuda-graph-bs-decode" not in cmd
    assert "--cuda-graph-bs-decode" not in mtp_cmd
    assert mtp_cmd[mtp_cmd.index("--cuda-graph-max-bs-decode") + 1] == "32"
    graph = ada.index("--cuda-graph-bs-decode") + 1
    assert ada[graph : graph + len(CUDAGRAPH_CAPTURE_SIZES)] == [
        str(size) for size in CUDAGRAPH_CAPTURE_SIZES
    ]


def test_sglang_context_clamps_to_the_checkpoint(tmp_path: Path) -> None:
    from modal_shared.serving.sglang import build_sglang_args

    checkpoint = tmp_path / "weights"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text(json.dumps({"max_position_embeddings": 4096}))
    cmd = build_sglang_args(
        model_path=str(checkpoint),
        served_name="deploy-1",
        port=8000,
        gpu_type="L4",
        max_model_len=8192,
        max_running_requests=4,
    )
    assert cmd[cmd.index("--context-length") + 1] == "4096"
    assert cmd[cmd.index("--max-running-requests") + 1] == "4"
    graph = cmd.index("--cuda-graph-bs-decode") + 1
    assert cmd[graph : graph + 3] == ["1", "2", "4"]
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "config.json").write_text(
        json.dumps({"text_config": {"max_position_embeddings": 2048}})
    )
    nested_cmd = build_sglang_args(
        model_path=str(nested),
        served_name="deploy-1",
        port=8000,
        max_model_len=8192,
    )
    assert nested_cmd[nested_cmd.index("--context-length") + 1] == "2048"


def test_dflash_argv_for_the_three_bases(tmp_path: Path) -> None:
    from modal_shared.serving.speculative import (
        VLLM_MTP_TOKENS,
        draft_for,
        draft_volume_path,
        mtp_for,
    )

    cmd = _args(
        _checkpoint(tmp_path),
        base_model="unsloth/Qwen3-4B",
        draft_model="/weights/.drafts/z-lab--Qwen3-4B-DFlash-b16",
        draft_tokens=15,
    )
    payload = json.loads(cmd[cmd.index("--speculative-config") + 1])
    assert payload == {
        "method": "dflash",
        "model": "/weights/.drafts/z-lab--Qwen3-4B-DFlash-b16",
        "num_speculative_tokens": 15,
    }
    assert "--cudagraph-capture-sizes" not in cmd
    mtp = _args(_checkpoint(tmp_path), base_model="Qwen/Qwen3.6-27B", mtp_tokens=VLLM_MTP_TOKENS)
    mtp_payload = json.loads(mtp[mtp.index("--speculative-config") + 1])
    assert mtp_payload == {"method": "mtp", "num_speculative_tokens": 1}
    assert "--cudagraph-capture-sizes" not in mtp
    assert "--speculative-config" not in _args(_checkpoint(tmp_path), base_model="Qwen/Qwen3.6-27B")
    for repo in (
        "Qwen/Qwen3.5-4B",
        "unsloth/Qwen3.5-4B",
        "Qwen/Qwen3.5-2B",
        "unsloth/Qwen3.5-2B",
        "Qwen/Qwen3.5-0.8B",
        "unsloth/Qwen3.5-0.8B",
        "Qwen/Qwen3.5-9B",
        "unsloth/Qwen3.5-9B",
        "Qwen/Qwen3.6-27B",
        "unsloth/Qwen3.6-27B",
    ):
        assert mtp_for(repo) == (3, 1, 4)
        assert draft_for(repo) is None
    assert draft_for("Qwen/Qwen3-4B") == ("z-lab/Qwen3-4B-DFlash-b16", 15)
    assert draft_volume_path("/weights", draft_for("Qwen/Qwen3-4B")[0]).startswith(
        "/weights/.drafts/"
    )
    assert draft_for("unsloth/Qwen3.8-27B") == ("z-lab/Qwen3.8-27B-DFlash2", 7)
    assert draft_for("Qwen/Qwen3.8-27B") == ("z-lab/Qwen3.8-27B-DFlash2", 7)
    assert draft_for("unsloth/Muse-Glimmer-30B") == ("z-lab/Muse-Glimmer-30B-DFlash2", 7)
    assert draft_for("unsloth/Llama-3.1-8B-Instruct") == (
        "z-lab/LLaMA3.1-8B-Instruct-DFlash-UltraChat",
        15,
    )
    assert draft_for("unsloth/gemma-4-26B-A4B-it") == ("z-lab/gemma-4-26B-A4B-it-DFlash", 15)
    assert draft_for("unsloth/gemma-4-31B-it") == ("z-lab/gemma-4-31B-it-DFlash", 15)
    assert draft_for("unsloth/Qwen3.5-27B") == ("z-lab/Qwen3.5-27B-DFlash", 15)
    assert draft_for("unsloth/Qwen3.5-27B", vram_gb=80) is None
    assert draft_for("Qwen/Qwen3.5-27B", vram_gb=80) is None
    assert draft_for("unsloth/Qwen3.5-27B", vram_gb=141) == ("z-lab/Qwen3.5-27B-DFlash", 15)
    assert draft_for("unsloth/Qwen3.5-35B-A3B") == ("z-lab/Qwen3.5-35B-A3B-DFlash", 15)
    assert draft_for("Qwen/Qwen3-14B") is None
    assert "--speculative-config" not in _args(_checkpoint(tmp_path), base_model="Qwen/Qwen3-14B")


def test_lora_slots_capped(tmp_path: Path) -> None:
    adapters = tuple((f"a{i}", f"/weights/a{i}") for i in range(MAX_LORA_SLOTS + 4))
    cmd = _args(_checkpoint(tmp_path), lora_adapters=adapters)
    assert cmd[cmd.index("--max-loras") + 1] == str(MAX_LORA_SLOTS)


def test_lora_slots_match_adapter_count_when_under_cap(tmp_path: Path) -> None:
    adapters = (("a", "/weights/a"), ("b", "/weights/b"))
    cmd = _args(_checkpoint(tmp_path), lora_adapters=adapters)
    assert cmd[cmd.index("--max-loras") + 1] == "2"


def test_no_lora_flags_without_adapters(tmp_path: Path) -> None:
    cmd = _args(_checkpoint(tmp_path))
    assert "--enable-lora" not in cmd
    assert "--lora-modules" not in cmd


def test_chat_template_read_from_adapter_dir(tmp_path: Path) -> None:
    """A LoRA finetune serves the stock base but must answer with its trained template."""
    base = _checkpoint(tmp_path)
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "chat_template.jinja").write_text("{{ messages }}")
    cmd = _args(
        base,
        lora_adapters=(("deploy-1", str(adapter)),),
        chat_template_dir=str(adapter),
    )
    assert cmd[cmd.index("--chat-template") + 1].startswith(str(adapter))


def test_decision_vllm_command_serves_one_adapter(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text(json.dumps({"r": 8}))
    cmd = decision_vllm_command(
        model_path="/tmp/serve",
        served_name="decision",
        adapter_path=str(adapter),
        max_lora_rank=0,
        max_model_len=4096,
    )
    assert cmd[cmd.index("--max-loras") + 1] == "1"
    assert cmd[cmd.index("--max-lora-rank") + 1] == "8"
    assert cmd[cmd.index("--lora-modules") + 1] == f"decision={adapter}"
    assert "--load-format" not in cmd
    base = decision_base_vllm_command(
        model_path="vllm-sr/d3-nano", served_name="vllm-sr/d3-nano", max_model_len=4096
    )
    assert "--enable-lora" not in base
    assert base[base.index("--served-model-name") + 1] == "vllm-sr/d3-nano"


def test_systemone_probabilities_follow_the_option_order() -> None:
    request = {
        "state": "The invoice is overdue.",
        "question": "What is the risk?",
        "kind": "choice",
        "options": ["low", "high"],
    }
    body = systemone_body(request, "decision")
    assert body["model"] == "decision"
    assert body["questions"]["0"]["criteria"] == {"low": "low", "high": "high"}
    assert probabilities_for(request, {"probabilities": {"low": 0.2, "high": 0.8}}) == [0.2, 0.8]
    noul = {**request, "kind": "noul"}
    assert probabilities_for(noul, {"noul": 0.75}) == [0.25, 0.75]


def test_binary_serving_keeps_custom_false_and_true_criteria():
    request = {"state": "state", "question": "Choose", "kind": "noul", "options": ["deny", "allow"]}
    question = systemone_body(request, "decision")["questions"]["0"]
    assert question["criteria"] == {"false": "deny", "true": "allow"}
    request["options"] = ["allow", "deny"]
    assert systemone_body(request, "decision")["questions"]["0"]["criteria"] == {
        "false": "allow",
        "true": "deny",
    }


def test_nemotron_expert_lora_is_marked_3d() -> None:
    from modal_shared.serving.nemotron_lora import mark

    class NemotronHForCausalLM:
        is_non_gated_moe = True

    mark(NemotronHForCausalLM)
    assert NemotronHForCausalLM.is_3d_moe_weight is True


def test_lfm3_hidden_dims_and_unsloth_target_rewrite(tmp_path: Path) -> None:
    from modal_shared.serving.lfm3_lora import hidden_dim, stacked_multiply
    from modal_shared.serving.sglang import normalize_sglang_adapter

    assert hidden_dim("in_proj", 2048, 7680) == (2048, 6144)
    assert hidden_dim("out_proj", 2048, 7680) == (2048, 2048)
    assert hidden_dim("w1", 2048, 7680) == (2048, 7680)
    assert hidden_dim("w3", 2048, 7680) == (2048, 7680)
    assert hidden_dim("w2", 2048, 7680) == (7680, 2048)
    assert hidden_dim("q_proj", 2048, 7680) is None
    assert stacked_multiply("in_proj") == 3
    assert stacked_multiply("w1") is None

    adapter = tmp_path / "adapter"
    adapter.mkdir()
    config = adapter / "adapter_config.json"
    config.write_text(json.dumps({"target_modules": "model\\.layers.*"}))
    normalize_sglang_adapter(adapter, keep_targets="q_proj,in_proj")
    assert json.loads(config.read_text())["target_modules"] == ["q_proj", "in_proj"]
    config.write_text(json.dumps({"target_modules": "model\\.layers.*"}))
    normalize_sglang_adapter(adapter)
    assert json.loads(config.read_text())["target_modules"] == "all-linear"
