import json

import pytest

from modal_shared.serving.args import VllmServeContext, build_vllm_args, lora_load_request


@pytest.mark.parametrize("fused", [False, True])
def test_adapter_request_declares_expert_format(tmp_path, fused):
    config = {"target_parameters": ["mlp.experts.gate_up_proj", "mlp.experts.down_proj"]}
    (tmp_path / "adapter_config.json").write_text(json.dumps(config if fused else {}))
    request = lora_load_request("adapter", str(tmp_path))
    assert request == {
        "lora_name": "adapter",
        "lora_path": str(tmp_path),
        "is_3d_lora_weight": fused,
    }


@pytest.mark.parametrize(
    ("base", "lora", "expected"),
    [
        ("Qwen/Qwen3-Coder-30B-A3B-Instruct", True, True),
        ("Qwen/Qwen3-Coder-30B-A3B-Instruct", False, False),
        ("Qwen/Qwen3-8B", True, False),
    ],
)
def test_mixed_expert_format_is_scoped_to_coder_adapters(base, lora, expected):
    args = build_vllm_args(
        VllmServeContext(
            model_path="/absent",
            model_name="deployment",
            base_model=base,
            max_model_len=512,
            port=8000,
            enable_lora=lora,
        )
    )
    assert ("--enable-mixed-moe-lora-format" in args) is expected
