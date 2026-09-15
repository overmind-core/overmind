from __future__ import annotations

import json
from pathlib import Path

from overbae.modal.register_model import _base_model_dir, _snapshot_is_complete


def _shard(d: Path, name: str, size: int = 16) -> None:
    (d / name).write_bytes(b"\0" * size)


def _write_sharded(d: Path, shards: list[str]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps({"model_type": "qwen2"}))
    (d / "model.safetensors.index.json").write_text(
        json.dumps({"weight_map": {f"layer.{i}.weight": s for i, s in enumerate(shards)}})
    )


def test_rejects_config_only(tmp_path: Path):
    d = tmp_path / "base"
    d.mkdir()
    (d / "config.json").write_text("{}")
    assert not _snapshot_is_complete(d)


def test_rejects_missing_shard(tmp_path: Path):
    d = tmp_path / "base"
    _write_sharded(d, ["a.safetensors", "b.safetensors"])
    _shard(d, "a.safetensors")  # b never landed
    assert not _snapshot_is_complete(d)


def test_rejects_zero_byte_shard(tmp_path: Path):
    d = tmp_path / "base"
    _write_sharded(d, ["a.safetensors", "b.safetensors"])
    _shard(d, "a.safetensors")
    _shard(d, "b.safetensors", size=0)
    assert not _snapshot_is_complete(d)


def test_rejects_in_flight_download(tmp_path: Path):
    d = tmp_path / "base"
    _write_sharded(d, ["a.safetensors"])
    _shard(d, "a.safetensors")
    (d / ".cache").mkdir()
    (d / ".cache" / "b.safetensors.incomplete").write_bytes(b"\0")
    assert not _snapshot_is_complete(d)


def test_accepts_complete_sharded(tmp_path: Path):
    d = tmp_path / "base"
    _write_sharded(d, ["a.safetensors", "b.safetensors"])
    _shard(d, "a.safetensors")
    _shard(d, "b.safetensors")
    assert _snapshot_is_complete(d)


def test_accepts_legacy_single_file_cache(tmp_path: Path):
    """No index (single-shard model) — must still count as cached."""
    d = tmp_path / "base"
    d.mkdir()
    (d / "config.json").write_text("{}")
    _shard(d, "model.safetensors")
    assert _snapshot_is_complete(d)


def test_rejects_missing_dir(tmp_path: Path):
    assert not _snapshot_is_complete(tmp_path / "nope")


def test_base_dir_is_flattened_repo_id():
    assert _base_model_dir("Qwen/Qwen2.5-72B-Instruct").name == "Qwen--Qwen2.5-72B-Instruct"
