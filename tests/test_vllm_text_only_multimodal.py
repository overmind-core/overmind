from __future__ import annotations

import json
from pathlib import Path

import pytest

modal = pytest.importorskip("modal")

from overbae.modal.modal_vllm_worker import (  # noqa: E402
    _is_text_only_finetune_of_multimodal_base,
    _normalize_model_blob,
)


def test_normalize_unifies_separators():
    assert "qwen3-5" in _normalize_model_blob("Qwen/Qwen3.5-0.8B")
    assert "qwen3-5" in _normalize_model_blob("ft-x-qwen3-5-0-8b")


def test_empty_base_model_uses_model_name(tmp_path: Path):
    # Mirrors qexnd23: meta.base_model="" but model_id contains qwen3-5.
    assert _is_text_only_finetune_of_multimodal_base("", str(tmp_path), "ft-bbcdec45-qwen3-5-0-8b")


def test_config_architecture_without_preprocessor(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "model_type": "qwen3_5",
                "architectures": ["Qwen3_5ForConditionalGeneration"],
            }
        )
    )
    assert _is_text_only_finetune_of_multimodal_base("", str(tmp_path), "ft-unknown")


def test_with_preprocessor_is_not_text_only(tmp_path: Path):
    (tmp_path / "preprocessor_config.json").write_text("{}")
    (tmp_path / "config.json").write_text(
        json.dumps({"model_type": "qwen3_5", "architectures": ["Qwen3_5ForConditionalGeneration"]})
    )
    assert not _is_text_only_finetune_of_multimodal_base(
        "Qwen/Qwen3.5-0.8B", str(tmp_path), "ft-x-qwen3-5-0-8b"
    )


def test_with_processor_config_is_not_text_only(tmp_path: Path):
    # Muse Glimmer ships processor_config.json (not preprocessor_config.json).
    (tmp_path / "processor_config.json").write_text("{}")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "model_type": "muse_glimmer",
                "architectures": ["MuseGlimmerForConditionalGeneration"],
            }
        )
    )
    assert not _is_text_only_finetune_of_multimodal_base(
        "unsloth/Muse-Glimmer-30B", str(tmp_path), "ft-x-muse-glimmer-30b"
    )


def test_plain_llama_not_flagged(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps({"model_type": "llama", "architectures": ["LlamaForCausalLM"]})
    )
    assert not _is_text_only_finetune_of_multimodal_base(
        "meta-llama/Llama-3.1-70B-Instruct", str(tmp_path), "ft-x-llama"
    )
