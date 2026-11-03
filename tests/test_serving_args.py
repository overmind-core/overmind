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
    from modal_shared.serving.engine import engine_for
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
    assert sglang_tool_parser("openai") == "gpt-oss"
    assert sglang_tool_parser("llama3_json") == "llama3"
    assert sglang_tool_parser("muse_glimmer") == "muse"
    assert cmd[cmd.index("--speculative-algorithm") + 1] == "DFLASH"
    assert cmd[cmd.index("--speculative-num-draft-tokens") + 1] == "15"
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


def test_dflash_argv_for_the_three_bases(tmp_path: Path) -> None:
    from modal_shared.serving.speculative import draft_for, draft_volume_path

    cmd = _args(
        _checkpoint(tmp_path),
        base_model="unsloth/Qwen3.5-4B",
        draft_model="/weights/.drafts/z-lab--Qwen3.5-4B-DFlash",
        draft_tokens=15,
    )
    payload = json.loads(cmd[cmd.index("--speculative-config") + 1])
    assert payload == {
        "method": "dflash",
        "model": "/weights/.drafts/z-lab--Qwen3.5-4B-DFlash",
        "num_speculative_tokens": 15,
    }
    assert "--cudagraph-capture-sizes" not in cmd
    for repo in (
        "Qwen/Qwen3.5-4B",
        "unsloth/Qwen3.5-4B",
        "Qwen/Qwen3.5-2B",
        "unsloth/Qwen3.5-2B",
        "Qwen/Qwen3.5-0.8B",
        "unsloth/Qwen3.5-0.8B",
    ):
        assert draft_for(repo) is not None
        assert draft_volume_path("/weights", draft_for(repo)[0]).startswith("/weights/.drafts/")
    assert draft_for("Qwen/Qwen3.5-9B") == ("z-lab/Qwen3.5-9B-DFlash", 15)
    assert draft_for("unsloth/Qwen3.5-9B") == ("z-lab/Qwen3.5-9B-DFlash", 15)
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
