"""File layouts below are VERBATIM from two real Baseten jobs: qjx77gw (final
adapter at the root plus nested checkpoint-N/ dirs) and qzprr8w (ONLY nested
checkpoint-N/ dirs — the root adapter save never synced to checkpoint storage).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

modal = pytest.importorskip("modal")

from overbae.modal.register_model import (  # noqa: E402
    _dir_has_weights,
    _name_is_weight,
    _zip_has_weights,
    resolve_artifact_dir,
)


def _touch(root: Path, *rels: str) -> None:
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")


def test_root_adapter_layout_like_qjx77gw(tmp_path):
    _touch(
        tmp_path,
        "README.md",
        "adapter_config.json",
        "adapter_model.safetensors",
        "tokenizer.json",
        "tokenizer_config.json",
        "checkpoint-9/adapter_config.json",
        "checkpoint-9/adapter_model.safetensors",
        "checkpoint-35/adapter_config.json",
        "checkpoint-35/adapter_model.safetensors",
    )
    d, is_lora = resolve_artifact_dir(tmp_path)
    assert d == tmp_path
    assert is_lora is True


def test_nested_only_layout_like_qzprr8w(tmp_path):
    _touch(
        tmp_path,
        "README.md",
        ".download_meta.json",
        "checkpoint-69/adapter_config.json",
        "checkpoint-69/adapter_model.safetensors",
        "checkpoint-138/adapter_config.json",
        "checkpoint-138/adapter_model.safetensors",
        "checkpoint-207/adapter_config.json",
        "checkpoint-207/adapter_model.safetensors",
        "checkpoint-273/adapter_config.json",
        "checkpoint-273/adapter_model.safetensors",
    )
    d, is_lora = resolve_artifact_dir(tmp_path)
    # Highest-step checkpoint wins — numeric compare, not lexicographic
    # (273 > 69 even though "273" < "69" as strings).
    assert d == tmp_path / "checkpoint-273"
    assert is_lora is True


def test_full_weights_at_root(tmp_path):
    _touch(tmp_path, "config.json", "model.safetensors", "tokenizer.json")
    d, is_lora = resolve_artifact_dir(tmp_path)
    assert d == tmp_path
    assert is_lora is False


def test_nested_full_weights(tmp_path):
    _touch(tmp_path, "checkpoint-10/config.json", "checkpoint-10/model.safetensors")
    d, is_lora = resolve_artifact_dir(tmp_path)
    assert d == tmp_path / "checkpoint-10"
    assert is_lora is False


def test_no_artifact_raises(tmp_path):
    _touch(tmp_path, "README.md", "tokenizer.json")
    with pytest.raises(FileNotFoundError):
        resolve_artifact_dir(tmp_path)


def test_name_is_weight():
    assert _name_is_weight("adapter_model.safetensors")
    assert _name_is_weight("model.safetensors")
    assert _name_is_weight("model-00001-of-00002.safetensors")
    assert not _name_is_weight("adapter_config.json")
    assert not _name_is_weight("tokenizer.json")


def test_dir_has_weights_ignores_nested_only(tmp_path):
    # Nested weights must not count for the root — resolve_artifact_dir decides
    # the nested fallback separately.
    _touch(tmp_path, "adapter_config.json", "checkpoint-1/adapter_model.safetensors")
    assert not _dir_has_weights(tmp_path)
    assert _dir_has_weights(tmp_path / "checkpoint-1")


def test_zip_has_weights_rejects_metadata_only(tmp_path):
    # Mirrors the 21 KB poison zip from ft-7cd1e3d2 (2026-07-28).
    bad = tmp_path / "checkpoint.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        for name in (
            "README.md",
            "adapter_config.json",
            "chat_template.jinja",
            "tokenizer_config.json",
            "training_args.bin",
        ):
            zf.writestr(name, b"x")
    assert not _zip_has_weights(bad)

    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("adapter_config.json", b"{}")
        zf.writestr("adapter_model.safetensors", b"weights")
    assert _zip_has_weights(good)


def test_spent_staging_drops_ready_adapter_and_merged(tmp_path):
    from overbae.modal.register_model import ADAPTERS_DIRNAME, STAGING_DIRNAME, spent_staging_names

    staging = tmp_path / STAGING_DIRNAME
    (staging / "adapter-job").mkdir(parents=True)
    (staging / "merged-job").mkdir()
    (staging / "in-flight").mkdir()
    (tmp_path / ADAPTERS_DIRNAME / "adapter-job").mkdir(parents=True)
    (tmp_path / ADAPTERS_DIRNAME / "adapter-job" / ".meta.json").write_text("{}")
    (tmp_path / "merged-job").mkdir()
    (tmp_path / "merged-job" / ".meta.json").write_text('{"ready": true}')
    assert set(spent_staging_names(tmp_path, max_age_s=10**12)) == {
        "adapter-job",
        "merged-job",
    }


def test_spent_staging_keeps_fresh_unzip_without_serving_copy(tmp_path):
    from overbae.modal.register_model import STAGING_DIRNAME, spent_staging_names

    child = tmp_path / STAGING_DIRNAME / "probe"
    child.mkdir(parents=True)
    (child / "adapter_config.json").write_text("{}")
    now = child.stat().st_mtime
    assert spent_staging_names(tmp_path, now=now + 60, max_age_s=3600) == []
    assert spent_staging_names(tmp_path, now=now + 4000, max_age_s=3600) == ["probe"]
