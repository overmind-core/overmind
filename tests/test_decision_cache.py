import os
from pathlib import Path

import pytest

from overbae.modal import decision_cache


def _snapshot(folder: Path, *, shard: str = "model.safetensors") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.json").write_text("{}")
    (folder / "tokenizer.json").write_text("{}")
    (folder / shard).write_bytes(b"weights")


def test_complete_snapshot_is_not_fetched_again(tmp_path: Path, monkeypatch):
    folder = tmp_path / "LiquidAI--d1-omni-600M"
    _snapshot(folder)
    calls = []

    def download(**_kwargs):
        calls.append(1)

    monkeypatch.setattr(decision_cache, "ROOT", tmp_path)
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "0")
    monkeypatch.setattr("huggingface_hub.snapshot_download", download)
    path, fetched = decision_cache.ensure("LiquidAI/d1-omni-600M")
    assert path == folder
    assert fetched is False
    assert calls == []
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_missing_snapshot_downloads_once(tmp_path: Path, monkeypatch):
    calls = []

    def download(**kwargs):
        calls.append(kwargs["repo_id"])
        _snapshot(Path(kwargs["local_dir"]))

    monkeypatch.setattr(decision_cache, "ROOT", tmp_path)
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "0")
    monkeypatch.setattr("huggingface_hub.snapshot_download", download)
    path, fetched = decision_cache.ensure("LiquidAI/d1-3B")
    assert fetched is True
    assert path == tmp_path / "LiquidAI--d1-3B"
    again, fetched_again = decision_cache.ensure("LiquidAI/d1-3B")
    assert again == path
    assert fetched_again is False
    assert calls == ["LiquidAI/d1-3B"]


def test_hub_conflict_keeps_the_volume_copy(tmp_path: Path, monkeypatch):
    calls = []

    def conflict(**kwargs):
        calls.append(kwargs["repo_id"])
        folder = Path(kwargs["local_dir"])
        _snapshot(folder)
        (folder / "config.json").write_text('{"revision": "cached"}')
        if len(calls) == 1:
            raise RuntimeError("etag conflict")
        raise AssertionError("hub was called again")

    monkeypatch.setattr(decision_cache, "ROOT", tmp_path)
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "0")
    monkeypatch.setattr("huggingface_hub.snapshot_download", conflict)
    path, fetched = decision_cache.ensure("LiquidAI/d1-3B")
    assert fetched is True
    assert (path / "config.json").read_text() == '{"revision": "cached"}'
    again, fetched_again = decision_cache.ensure("LiquidAI/d1-3B")
    assert again == path
    assert fetched_again is False
    assert calls == ["LiquidAI/d1-3B"]


def test_incomplete_conflict_is_not_reused(tmp_path: Path, monkeypatch):
    folder = tmp_path / "LiquidAI--d1-omni-600M"
    folder.mkdir()
    (folder / "config.json").write_text("{}")
    (folder / "model.safetensors.incomplete").write_bytes(b"partial")

    def conflict(**_kwargs):
        raise RuntimeError("etag conflict")

    monkeypatch.setattr(decision_cache, "ROOT", tmp_path)
    monkeypatch.setattr("huggingface_hub.snapshot_download", conflict)
    with pytest.raises(RuntimeError, match="etag conflict"):
        decision_cache.ensure("LiquidAI/d1-omni-600M")


def test_local_checkpoint_is_not_fetched(tmp_path: Path, monkeypatch):
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()

    def download(**_kwargs):
        raise AssertionError("hub")

    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setattr("huggingface_hub.snapshot_download", download)
    path, fetched = decision_cache.ensure(str(checkpoint))
    assert path == checkpoint
    assert fetched is False
