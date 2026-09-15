"""Skipped when torch/peft/safetensors are not installed (local Django venv)."""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("peft")
pytest.importorskip("safetensors")

from safetensors.torch import save_file  # noqa: E402

from overbae.modal.weight_ops import (  # noqa: E402
    fix_model_type,
    merge_lora_weights,
    prepare_fp8_weights,
)


def _write_tiny_lora_pair(tmp: Path) -> tuple[Path, Path]:
    base = tmp / "base"
    adapter = tmp / "adapter"
    base.mkdir()
    adapter.mkdir()

    w = torch.randn(4, 4, dtype=torch.float32)
    save_file({"model.layers.0.self_attn.q_proj.weight": w}, base / "model.safetensors")
    (base / "config.json").write_text(json.dumps({"model_type": "llama", "hidden_size": 4}))
    (base / "tokenizer_config.json").write_text(
        json.dumps({"tokenizer_class": "PreTrainedTokenizerFast"})
    )

    r, alpha = 2, 4
    a = torch.randn(r, 4, dtype=torch.float32)
    b = torch.randn(4, r, dtype=torch.float32)
    save_file(
        {
            "model.layers.0.self_attn.q_proj.lora_A.weight": a,
            "model.layers.0.self_attn.q_proj.lora_B.weight": b,
        },
        adapter / "adapter_model.safetensors",
    )
    (adapter / "adapter_config.json").write_text(
        json.dumps(
            {
                "peft_type": "LORA",
                "r": r,
                "lora_alpha": alpha,
                "lora_dropout": 0.0,
                "target_modules": ["q_proj"],
                "base_model_name_or_path": "test/base",
            }
        )
    )
    (adapter / "chat_template.jinja").write_text("{{ messages }}")
    return base, adapter


def test_merge_lora_weights_applies_delta(tmp_path: Path):
    base, adapter = _write_tiny_lora_pair(tmp_path)
    out = tmp_path / "merged"

    from safetensors import safe_open

    with safe_open(base / "model.safetensors", framework="torch") as h:
        w = h.get_tensor("model.layers.0.self_attn.q_proj.weight")
    with safe_open(adapter / "adapter_model.safetensors", framework="torch") as h:
        a = h.get_tensor("model.layers.0.self_attn.q_proj.lora_A.weight")
        b = h.get_tensor("model.layers.0.self_attn.q_proj.lora_B.weight")
    expected = w + (4 / 2) * (b @ a)

    merge_lora_weights(base, adapter, out)

    with safe_open(out / "model.safetensors", framework="torch") as h:
        got = h.get_tensor("model.layers.0.self_attn.q_proj.weight")
    assert torch.allclose(got, expected, atol=1e-5)
    assert (out / "chat_template.jinja").exists()
    assert (out / "config.json").exists()


def test_merge_lora_weights_gpt_oss_modulelist_experts(tmp_path: Path):
    """BnB ModuleList LoRA (down_projs.N) folds into fused BF16 experts.down_proj."""
    base = tmp_path / "base"
    adapter = tmp_path / "adapter"
    base.mkdir()
    adapter.mkdir()

    n_experts, hidden, intermediate = 2, 4, 4
    # Match Hub BF16 layout: down_proj (E, hidden, inter), gate_up (E, hidden, 2*inter)
    # i.e. gate_up is Linear.weight.T relative to nn.Linear(out=2*inter, in=hidden).
    down = torch.randn(n_experts, hidden, intermediate, dtype=torch.float32)
    gate_up = torch.randn(n_experts, hidden, 2 * intermediate, dtype=torch.float32)
    save_file(
        {
            "model.layers.0.mlp.experts.down_proj": down,
            "model.layers.0.mlp.experts.gate_up_proj": gate_up,
        },
        base / "model.safetensors",
    )
    (base / "config.json").write_text(json.dumps({"model_type": "gpt_oss", "hidden_size": hidden}))

    r, alpha = 2, 4
    scale = alpha / r
    tensors: dict[str, torch.Tensor] = {}
    expected_down = down.clone()
    expected_gate = gate_up.clone()
    for i in range(n_experts):
        a_d = torch.randn(r, intermediate, dtype=torch.float32)
        b_d = torch.randn(hidden, r, dtype=torch.float32)
        a_g = torch.randn(r, hidden, dtype=torch.float32)
        b_g = torch.randn(2 * intermediate, r, dtype=torch.float32)
        tensors[f"model.layers.0.mlp.experts.down_projs.{i}.lora_A.weight"] = a_d
        tensors[f"model.layers.0.mlp.experts.down_projs.{i}.lora_B.weight"] = b_d
        tensors[f"model.layers.0.mlp.experts.gate_up_projs.{i}.lora_A.weight"] = a_g
        tensors[f"model.layers.0.mlp.experts.gate_up_projs.{i}.lora_B.weight"] = b_g
        expected_down[i] = expected_down[i] + scale * (b_d @ a_d)
        expected_gate[i] = expected_gate[i] + scale * (b_g @ a_g).T
    save_file(tensors, adapter / "adapter_model.safetensors")
    (adapter / "adapter_config.json").write_text(
        json.dumps(
            {
                "peft_type": "LORA",
                "r": r,
                "lora_alpha": alpha,
                "lora_dropout": 0.0,
                "target_modules": "down_projs|gate_up_projs",
                "base_model_name_or_path": "unsloth/gpt-oss-20b-BF16",
            }
        )
    )

    out = tmp_path / "merged"
    merge_lora_weights(base, adapter, out)

    from safetensors import safe_open

    with safe_open(out / "model.safetensors", framework="torch") as h:
        got_down = h.get_tensor("model.layers.0.mlp.experts.down_proj")
        got_gate = h.get_tensor("model.layers.0.mlp.experts.gate_up_proj")
    assert torch.allclose(got_down, expected_down, atol=1e-5)
    assert torch.allclose(got_gate, expected_gate, atol=1e-5)


def test_merge_lora_weights_gemma4_clippable_linear_layout(tmp_path: Path):
    """Base stores ``q_proj.linear.weight``; adapter keys omit the ``.linear`` segment."""
    base = tmp_path / "base"
    adapter = tmp_path / "adapter"
    base.mkdir()
    adapter.mkdir()

    w = torch.randn(4, 4, dtype=torch.float32)
    save_file(
        {"model.layers.0.self_attn.q_proj.linear.weight": w},
        base / "model.safetensors",
    )
    (base / "config.json").write_text(json.dumps({"model_type": "gemma4", "hidden_size": 4}))

    r, alpha = 2, 4
    a = torch.randn(r, 4, dtype=torch.float32)
    b = torch.randn(4, r, dtype=torch.float32)
    save_file(
        {
            "model.layers.0.self_attn.q_proj.lora_A.weight": a,
            "model.layers.0.self_attn.q_proj.lora_B.weight": b,
        },
        adapter / "adapter_model.safetensors",
    )
    (adapter / "adapter_config.json").write_text(
        json.dumps(
            {
                "peft_type": "LORA",
                "r": r,
                "lora_alpha": alpha,
                "lora_dropout": 0.0,
                "target_modules": ["q_proj"],
                "base_model_name_or_path": "test/gemma4",
            }
        )
    )

    out = tmp_path / "merged"
    merge_lora_weights(base, adapter, out)

    from safetensors import safe_open

    with safe_open(out / "model.safetensors", framework="torch") as h:
        got = h.get_tensor("model.layers.0.self_attn.q_proj.linear.weight")
    expected = w + (alpha / r) * (b @ a)
    assert torch.allclose(got, expected, atol=1e-5)


def _write_sharded_lora_pair(tmp: Path, n_shards: int = 5) -> tuple[Path, Path, dict]:
    """Multi-shard base + adapter touching a layer in EVERY shard, so the merge
    runs one worker thread per shard against the shared adapter dict."""
    base = tmp / "base_sharded"
    adapter = tmp / "adapter_sharded"
    base.mkdir()
    adapter.mkdir()

    r, alpha = 2, 4
    expected: dict = {}
    adapter_tensors: dict = {}
    weight_map: dict = {}
    for i in range(n_shards):
        name = f"model.layers.{i}.self_attn.q_proj.weight"
        shard = f"model-{i:05d}-of-{n_shards:05d}.safetensors"
        w = torch.randn(4, 4, dtype=torch.float32)
        a = torch.randn(r, 4, dtype=torch.float32)
        b = torch.randn(4, r, dtype=torch.float32)
        save_file({name: w}, base / shard)
        weight_map[name] = shard
        adapter_tensors[f"model.layers.{i}.self_attn.q_proj.lora_A.weight"] = a
        adapter_tensors[f"model.layers.{i}.self_attn.q_proj.lora_B.weight"] = b
        expected[name] = w + (alpha / r) * (b @ a)

    (base / "model.safetensors.index.json").write_text(json.dumps({"weight_map": weight_map}))
    (base / "config.json").write_text(json.dumps({"model_type": "llama", "hidden_size": 4}))
    save_file(adapter_tensors, adapter / "adapter_model.safetensors")
    (adapter / "adapter_config.json").write_text(
        json.dumps(
            {
                "peft_type": "LORA",
                "r": r,
                "lora_alpha": alpha,
                "lora_dropout": 0.0,
                "target_modules": ["q_proj"],
                "base_model_name_or_path": "test/base",
            }
        )
    )
    return base, adapter, expected


def test_merge_lora_weights_parallel_shards_match_serial(tmp_path: Path):
    """Unconsumed adapter keys would take the PEFT fallback, which needs
    transformers to load a real model and therefore errors out here."""
    from safetensors import safe_open

    base, adapter, expected = _write_sharded_lora_pair(tmp_path)
    out = tmp_path / "merged_sharded"

    merge_lora_weights(base, adapter, out)

    for name, want in expected.items():
        shard = json.loads((base / "model.safetensors.index.json").read_text())["weight_map"][name]
        with safe_open(out / shard, framework="torch") as h:
            assert torch.allclose(h.get_tensor(name), want, atol=1e-5), name


def test_fix_model_type_patches_qwen35(tmp_path: Path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"model_type": "qwen3_5_text"}))
    fix_model_type(d)
    assert json.loads((d / "config.json").read_text())["model_type"] == "qwen3_5"


def test_fix_model_type_keeps_lightning_layers_block_type(tmp_path: Path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "config.json").write_text(
        json.dumps(
            {
                "model_type": "nemotron_h",
                "layers_block_type": ["mamba", "moe", "attention"],
            }
        )
    )
    fix_model_type(d)
    cfg = json.loads((d / "config.json").read_text())
    assert cfg["layers_block_type"] == ["mamba", "moe", "attention"]


def test_fix_model_type_drops_nano_layers_block_type(tmp_path: Path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "config.json").write_text(
        json.dumps(
            {
                "model_type": "nemotron_h",
                "hybrid_override_pattern": "M-M-M*-",
                "layers_block_type": ["mamba", "mamba", "mamba", "attention"],
            }
        )
    )
    fix_model_type(d)
    cfg = json.loads((d / "config.json").read_text())
    assert "layers_block_type" not in cfg
    assert cfg["hybrid_override_pattern"] == "M-M-M*-"


def test_overlay_full_weights_fills_missing_from_base(tmp_path: Path):
    from safetensors import safe_open

    from overbae.modal.weight_ops import overlay_full_weights

    base = tmp_path / "base"
    full = tmp_path / "full"
    out = tmp_path / "out"
    base.mkdir()
    full.mkdir()

    trained = torch.ones(2, 2)
    frozen = torch.full((2, 2), 7.0)
    save_file(
        {
            "language_model.layers.0.q_proj.weight": torch.zeros(2, 2),
            "language_model.layers.0.k_norm.weight": frozen,
        },
        base / "model.safetensors",
    )
    (base / "config.json").write_text(json.dumps({"model_type": "gemma4", "from": "base"}))
    save_file(
        {"language_model.layers.0.q_proj.weight": trained},
        full / "model.safetensors",
    )
    (full / "config.json").write_text(json.dumps({"model_type": "gemma4", "from": "ft"}))

    overlay_full_weights(base, full, out)

    with safe_open(out / "model.safetensors", framework="torch") as h:
        assert torch.equal(h.get_tensor("language_model.layers.0.q_proj.weight"), trained)
        assert torch.equal(h.get_tensor("language_model.layers.0.k_norm.weight"), frozen)
    assert json.loads((out / "config.json").read_text())["from"] == "ft"


def test_prepare_fp8_full_path_overlays_when_base_present(tmp_path: Path):
    base = tmp_path / "base"
    src = tmp_path / "full"
    base.mkdir()
    src.mkdir()
    save_file(
        {"a.weight": torch.zeros(2, 2), "b.weight": torch.ones(2, 2)}, base / "model.safetensors"
    )
    (base / "config.json").write_text(json.dumps({"model_type": "llama"}))
    save_file({"a.weight": torch.full((2, 2), 3.0)}, src / "model.safetensors")
    (src / "config.json").write_text(json.dumps({"model_type": "llama"}))
    out = tmp_path / "fp8_out"
    overlay_calls: list[bool] = []

    def _fake_quantize(source_dir, output_dir, **_kwargs):
        from safetensors import safe_open

        with safe_open(source_dir / "model.safetensors", framework="torch") as h:
            assert set(h.keys()) == {"a.weight", "b.weight"}
            assert float(h.get_tensor("a.weight")[0, 0]) == 3.0
            assert float(h.get_tensor("b.weight")[0, 0]) == 1.0
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "config.json").write_text((source_dir / "config.json").read_text())

    from overbae.modal.weight_ops import overlay_full_weights as real_overlay

    def _track(*args, **kwargs):
        overlay_calls.append(True)
        return real_overlay(*args, **kwargs)

    with (
        patch("overbae.modal.weight_ops.overlay_full_weights", side_effect=_track),
        patch("overbae.modal.weight_ops.quantize_checkpoint", side_effect=_fake_quantize),
    ):
        meta = prepare_fp8_weights(
            source_dir=src, output_dir=out, is_lora=False, base_model_path=base
        )

    assert overlay_calls == [True]
    assert meta["merge_method"] == "full_overlay"


def test_prepare_fp8_full_path_calls_quantize(tmp_path: Path):
    src = tmp_path / "full"
    src.mkdir()
    (src / "config.json").write_text(json.dumps({"model_type": "llama"}))
    out = tmp_path / "fp8_out"

    def _fake_quantize(source_dir, output_dir, **_kwargs):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "config.json").write_text((source_dir / "config.json").read_text())
        (output_dir / "model.safetensors").write_bytes(b"fake")

    with patch("overbae.modal.weight_ops.quantize_checkpoint", side_effect=_fake_quantize):
        meta = prepare_fp8_weights(source_dir=src, output_dir=out, is_lora=False)

    assert meta["quantization"] == "fp8"
    assert meta["is_lora"] is False
    assert (out / "config.json").exists()


def test_prepare_fp8_lora_path_merges_then_quantizes(tmp_path: Path):
    base, adapter = _write_tiny_lora_pair(tmp_path)
    out = tmp_path / "fp8_out"
    merge_calls: list[bool] = []

    def _fake_quantize(source_dir, output_dir, **_kwargs):
        assert (source_dir / "model.safetensors").exists()
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "config.json").write_text((source_dir / "config.json").read_text())

    real_merge = merge_lora_weights

    def _tracking_merge(*args, **kwargs):
        merge_calls.append(True)
        return real_merge(*args, **kwargs)

    with (
        patch("overbae.modal.weight_ops.merge_lora_weights", side_effect=_tracking_merge),
        patch("overbae.modal.weight_ops.quantize_checkpoint", side_effect=_fake_quantize),
    ):
        meta = prepare_fp8_weights(
            source_dir=adapter,
            output_dir=out,
            is_lora=True,
            base_model_path=base,
        )

    assert merge_calls == [True]
    assert meta["quantization"] == "fp8"
    assert meta["is_lora"] is False
    assert meta["merge_method"] == "safetensors"


def test_load_lora_config_strips_unknown_unsloth_keys(tmp_path: Path):
    base, adapter = _write_tiny_lora_pair(tmp_path)
    cfg_path = adapter / "adapter_config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["monteclora_config"] = {"enabled": True}
    cfg["velora_config"] = {"rank": 1}
    cfg_path.write_text(json.dumps(cfg))

    from overbae.modal.weight_ops import _load_lora_config

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        loaded = _load_lora_config(adapter)
    assert loaded.r == 2
    assert loaded.lora_alpha == 4
    assert not any("monteclora" in str(w.message) for w in caught)
