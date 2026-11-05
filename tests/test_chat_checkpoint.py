import json

import pytest

from modal_shared.chat_checkpoint import read_checkpoint, seal_checkpoint

pytestmark = pytest.mark.covers("model-runtime")


@pytest.mark.parametrize("adapter", [False, True])
def test_checkpoint_publication_requires_all_shards_and_tokenizer(tmp_path, adapter):
    config = "adapter_config.json" if adapter else "config.json"
    prefix = "adapter_model" if adapter else "model"
    (tmp_path / config).write_text("{}")
    (tmp_path / "tokenizer_config.json").write_text("{}")
    (tmp_path / "tokenizer.json").write_text("{}")
    (tmp_path / f"{prefix}.safetensors.index.json").write_text(
        json.dumps({"weight_map": {"a": "first.safetensors", "b": "second.safetensors"}})
    )
    (tmp_path / "first.safetensors").write_bytes(b"first weights")
    with pytest.raises(ValueError, match="regular immutable file"):
        seal_checkpoint(tmp_path)
    assert not (tmp_path / "checkpoint.json").exists()
    (tmp_path / "second.safetensors").write_bytes(b"second weights")
    manifest = seal_checkpoint(tmp_path)
    assert read_checkpoint(tmp_path)["identity"] == manifest["identity"]
    (tmp_path / "second.safetensors").write_bytes(b"truncated")
    with pytest.raises(ValueError, match="changed"):
        read_checkpoint(tmp_path)
