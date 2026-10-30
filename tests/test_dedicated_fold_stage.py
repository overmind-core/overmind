"""Dedicated LoRA serves the base shards through symlinks. Weights are not copied."""

from pathlib import Path

from modal_shared.serving.stage import stage_base


def test_stage_links_shards_and_overlays_the_adapter_template(tmp_path: Path):
    base = tmp_path / "base"
    adapter = tmp_path / "adapter"
    base.mkdir()
    adapter.mkdir()
    shard = base / "model-00001-of-00001.safetensors"
    shard.write_bytes(b"weights")
    (base / "config.json").write_text('{"model_type": "qwen2"}')
    (adapter / "adapter_model.safetensors").write_bytes(b"lora")
    (adapter / "adapter_config.json").write_text('{"r": 8, "lora_alpha": 16}')
    (adapter / "chat_template.jinja").write_text("adapter template")

    staged = stage_base(base, adapter, tmp_path / "serve")

    link = staged / shard.name
    assert link.is_symlink()
    assert link.resolve() == shard.resolve()
    assert (staged / "config.json").read_text() == '{"model_type": "qwen2"}'
    assert (staged / "chat_template.jinja").read_text() == "adapter template"
    assert link.read_bytes() == b"weights"
