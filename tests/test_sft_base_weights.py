from __future__ import annotations

import sys
from pathlib import Path

# sft_assets is uploaded to Modal as a flat directory, so its modules import each
# other by bare name and only resolve with the directory itself on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "overbae" / "services" / "sft_assets"))

from basepath import base_weights_for  # noqa: E402

MODEL = "Qwen/Qwen3-1.7B"
DIRNAME = "Qwen--Qwen3-1.7B"


def _snapshot(root: Path, *, name: str = DIRNAME, weights: bool = True, partial: bool = False):
    path = root / name
    path.mkdir(parents=True)
    (path / "config.json").write_text("{}")
    if weights:
        (path / "model.safetensors").write_bytes(b"\x00")
    if partial:
        (path / "model-00002-of-00002.safetensors.incomplete").write_bytes(b"")
    return path


def test_falls_back_to_the_hub_id_when_nothing_is_staged(monkeypatch):
    monkeypatch.delenv("BASE_MODEL_PATH", raising=False)
    assert base_weights_for(MODEL) == MODEL


def test_uses_the_staged_snapshot(monkeypatch, tmp_path):
    staged = _snapshot(tmp_path)
    monkeypatch.setenv("BASE_MODEL_PATH", str(staged))
    assert base_weights_for(MODEL) == str(staged)


def test_ignores_a_snapshot_for_a_different_model(monkeypatch, tmp_path):
    """env_overrides can swap MODEL_ID after the worker resolved the path; loading another
    model's weights under this model's identity would corrupt the run silently."""
    staged = _snapshot(tmp_path)
    monkeypatch.setenv("BASE_MODEL_PATH", str(staged))
    assert base_weights_for("openai/gpt-oss-20b") == "openai/gpt-oss-20b"


def test_empty_env_is_not_treated_as_a_path(monkeypatch):
    monkeypatch.setenv("BASE_MODEL_PATH", "")
    assert base_weights_for(MODEL) == MODEL


def test_missing_directory_falls_back(monkeypatch, tmp_path):
    """The prefetch is asynchronous — the path is named before it exists."""
    monkeypatch.setenv("BASE_MODEL_PATH", str(tmp_path / DIRNAME))
    assert base_weights_for(MODEL) == MODEL


def test_config_without_weights_falls_back(monkeypatch, tmp_path):
    """snapshot_download writes the small files first, so config.json alone proves nothing."""
    staged = _snapshot(tmp_path, weights=False)
    monkeypatch.setenv("BASE_MODEL_PATH", str(staged))
    assert base_weights_for(MODEL) == MODEL


def test_in_flight_download_falls_back(monkeypatch, tmp_path):
    staged = _snapshot(tmp_path, partial=True)
    monkeypatch.setenv("BASE_MODEL_PATH", str(staged))
    assert base_weights_for(MODEL) == MODEL
