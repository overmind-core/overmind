"""CUDA graph capture and shared-base LoRA argv."""

from __future__ import annotations

import json
from pathlib import Path

from modal_shared.serving.args import (
    CUDAGRAPH_CAPTURE_SIZES,
    MAX_LORA_SLOTS,
    VllmServeContext,
    build_vllm_args,
)


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


def test_base_keeps_its_name_without_adapters(tmp_path: Path) -> None:
    cmd = _args(_checkpoint(tmp_path))
    assert cmd[cmd.index("--served-model-name") + 1] == "deploy-1"


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
