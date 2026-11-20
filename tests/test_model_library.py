"""Public model library: provider labels and which catalog entries it lists."""

import pytest

from overbae.services.model_library import _provider_from_id, public_model_catalog

pytestmark = pytest.mark.covers("model-runtime")


def test_unsloth_hosted_models_resolve_to_their_vendor():
    assert _provider_from_id("unsloth/Muse-Glimmer-30B") == "Meta"
    assert _provider_from_id("nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B") == "NVIDIA"
    assert _provider_from_id("unsloth/NVIDIA-Nemotron-3.5-Lightning-30B-A3B") == "NVIDIA"
    assert _provider_from_id("openai/gpt-oss-20b") == "Open AI"
    assert _provider_from_id("google/gemma-4-E2B-it") == "Google"


def test_non_catalog_unsloth_ids_keep_org_fallback():
    # Muse → Meta, Nemotron → NVIDIA; other unsloth-hosted ids keep the org fallback.
    assert _provider_from_id("unsloth/gemma-4-E2B-it") == "Unsloth"
    assert _provider_from_id("unsloth/gpt-oss-20b-BF16") == "Unsloth"


def test_public_catalog_lists_each_open_weight_model_once():
    listed = [m["id"] for m in public_model_catalog() if m["category"] == "open-weight"]
    assert listed
    assert len(listed) == len(set(listed))
