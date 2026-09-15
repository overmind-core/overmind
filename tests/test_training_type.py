"""A model can support LoRA at a longer context than full fine-tuning — these tests
pin the per-training-kind restriction against a dataset's longest row."""

from __future__ import annotations

from overbae.modal.training_type import (
    dataset_training_type,
    normalize_training_type,
    training_context_length,
    training_enabled,
    usable_training_kinds,
)

# Full's smaller context than LoRA's is the exact shape the onboarding skill
# describes (LoRA's lighter optimizer state leaves more VRAM for activations).
_ENTRY = {
    "id": "example/model",
    "context_length_sft": 131072,
    "training_type": {
        "full": {"enabled": True, "context_length": 4096, "validated_context_length": True},
        "lora": {"enabled": True, "context_length": 32768, "validated_context_length": True},
    },
}


def test_normalize_training_type_carries_context_fields():
    tt = normalize_training_type(_ENTRY["training_type"])
    assert tt["full"] == {"enabled": True, "context_length": 4096, "validated_context_length": True}
    assert tt["lora"] == {
        "enabled": True,
        "context_length": 32768,
        "validated_context_length": True,
    }


def test_normalize_training_type_defaults_missing_kind():
    assert normalize_training_type({"lora": {"enabled": True}}) == {
        "lora": {"enabled": True, "context_length": None, "validated_context_length": False},
        "full": {"enabled": False, "context_length": None, "validated_context_length": False},
    }


def test_training_context_length_prefers_per_kind_over_flat():
    assert training_context_length(_ENTRY, "full") == 4096
    assert training_context_length(_ENTRY, "lora") == 32768


def test_training_context_length_falls_back_to_flat_model_context():
    entry = {"context_length_sft": 40960, "training_type": {"lora": {"enabled": True}}}
    assert training_context_length(entry, "lora") == 40960


def test_training_enabled_ignores_context_length():
    # Catalog-level enablement never depends on any particular dataset.
    assert training_enabled(_ENTRY, "lora") is True
    assert training_enabled(_ENTRY, "full") is True


def test_usable_training_kinds_offers_only_lora_when_full_context_too_small():
    # Between full's 4096 and LoRA's 32768 — the user's exact scenario.
    assert usable_training_kinds(_ENTRY, max_tokens=10_000, headroom=256) == ["lora"]


def test_usable_training_kinds_offers_both_when_row_fits_full_too():
    assert usable_training_kinds(_ENTRY, max_tokens=2000, headroom=256) == ["lora", "full"]


def test_usable_training_kinds_empty_past_every_kind():
    assert usable_training_kinds(_ENTRY, max_tokens=1_000_000, headroom=256) == []


def test_usable_training_kinds_ignores_context_without_max_tokens():
    assert usable_training_kinds(_ENTRY) == ["lora", "full"]


def test_dataset_training_type_disables_only_the_kind_that_overflows():
    tt = dataset_training_type(_ENTRY, max_tokens=10_000, headroom=256)
    assert tt["lora"]["enabled"] is True
    assert tt["full"]["enabled"] is False
    # Context/validated fields still describe the catalog, not the dataset.
    assert tt["full"]["context_length"] == 4096


def test_dataset_training_type_never_enables_a_catalog_disabled_kind():
    entry = {
        "training_type": {
            "full": {"enabled": False, "context_length": 131072},
            "lora": {"enabled": True, "context_length": 131072},
        }
    }
    tt = dataset_training_type(entry, max_tokens=100, headroom=0)
    assert tt["full"]["enabled"] is False
    assert tt["lora"]["enabled"] is True
