from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "overbae" / "services" / "sft_assets"))


def test_e2b_lora_enables_torch_gc(monkeypatch):
    monkeypatch.setenv("MODEL_ID", "unsloth/gemma-4-E2B-it")
    from families.gemma4 import hooks

    assert hooks.peft_gradient_checkpointing() is True
    cfg = hooks.sft_config_overrides(use_lora=True)
    assert cfg["gradient_checkpointing"] is True
    assert cfg["optim"] == "adamw_8bit"


def test_e2b_full_keeps_trainer_gc_off(monkeypatch):
    monkeypatch.setenv("MODEL_ID", "google/gemma-4-E2B-it")
    from families.gemma4 import hooks

    assert hooks.sft_config_overrides(use_lora=False)["gradient_checkpointing"] is False


def test_e4b_lora_enables_torch_gc(monkeypatch):
    monkeypatch.setenv("MODEL_ID", "unsloth/gemma-4-E4B-it")
    from families.gemma4 import hooks

    assert hooks.peft_gradient_checkpointing() is True
    assert hooks.sft_config_overrides(use_lora=True)["gradient_checkpointing"] is True


def test_e4b_full_keeps_trainer_gc_off(monkeypatch):
    monkeypatch.setenv("MODEL_ID", "google/gemma-4-E4B-it")
    from families.gemma4 import hooks

    assert hooks.sft_config_overrides(use_lora=False)["gradient_checkpointing"] is False


def test_12b_lora_enables_torch_gc(monkeypatch):
    monkeypatch.setenv("MODEL_ID", "unsloth/gemma-4-12b-it")
    from families.gemma4 import hooks

    assert hooks.peft_gradient_checkpointing() is True
    assert hooks.sft_config_overrides(use_lora=True)["gradient_checkpointing"] is True


def test_12b_full_enables_trainer_gc(monkeypatch):
    monkeypatch.setenv("MODEL_ID", "google/gemma-4-12B-it")
    from families.gemma4 import hooks

    assert hooks.sft_config_overrides(use_lora=False)["gradient_checkpointing"] is True


def test_a4b_lora_enables_torch_gc(monkeypatch):
    monkeypatch.setenv("MODEL_ID", "unsloth/gemma-4-26B-A4B-it")
    from families.gemma4 import hooks

    assert hooks.peft_gradient_checkpointing() is True
    assert hooks.sft_config_overrides(use_lora=True)["gradient_checkpointing"] is True
    assert hooks.peft_lora_dropout(0.05, model_id="unsloth/gemma-4-26B-A4B-it") == 0.0
