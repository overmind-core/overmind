from __future__ import annotations

from overbae.tasks.base_models import catalog_base_models, missing_base_models


def test_catalog_resolves_to_download_ids_not_picker_ids():
    """`.base_models/` is keyed on what actually gets downloaded — for Modal the unsloth
    mirror — while the catalog id is only what a user picks."""
    bases = catalog_base_models()

    assert bases
    assert "Qwen/Qwen3-8B" not in bases
    assert "unsloth/Qwen3-8B" in bases


def test_catalog_is_deduped():
    bases = catalog_base_models()

    assert len(bases) == len(set(bases))


def test_missing_excludes_what_is_already_staged():
    bases = catalog_base_models()

    assert missing_base_models(bases) == []
    assert missing_base_models([]) == bases


def test_missing_ignores_staged_bases_outside_the_catalog():
    """A base left over from a retired catalog entry is not a reason to re-fetch anything."""
    bases = catalog_base_models()

    assert missing_base_models([*bases, "some/retired-model"]) == []
