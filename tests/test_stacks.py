from __future__ import annotations

import json
from pathlib import Path

from modal_shared.modelfam import resolve
from modal_shared.modelfam.registry import all_families
from modal_shared.stacks import (
    GPU_CLASS_MAP,
    GPU_TIER,
    SERVE_CLASS_MAP,
    SERVE_STACKS,
    TRAIN_CANARIES,
    TRAIN_FUNCTION_NAMES,
    TRAIN_STACKS,
    WORKER_CLS,
    normalize_train_stack,
    train_function_name,
    worker_cls_name,
)

_MODELS_JSON = Path(__file__).resolve().parents[1] / "overbae" / "modal" / "models.json"


def test_normalize_train_stack_aliases() -> None:
    assert normalize_train_stack("default") == "u2026_8_18"
    assert normalize_train_stack("gemma4") == "u2026_8_tf510"
    assert normalize_train_stack("nemotron35") == "u2026_8_tf510"
    assert normalize_train_stack("muse") == "u2026_8_tf515"
    assert normalize_train_stack("gpt_oss") == "u2026_8_gptoss"
    assert normalize_train_stack("u2026_8_tf510") == "u2026_8_tf510"
    assert train_function_name("gemma4") == "sft_u2026_8_tf510"
    assert train_function_name("nemotron35") == "sft_u2026_8_tf510"
    assert train_function_name("stock") == "sft_stock"


def test_every_family_train_image_is_a_known_stack() -> None:
    for fam in (*all_families(), resolve("totally/unknown-model")):
        assert fam.train_image in TRAIN_STACKS, fam.key
        assert fam.serve_image in SERVE_STACKS, fam.key


def test_train_function_names_cover_stacks() -> None:
    assert set(TRAIN_FUNCTION_NAMES) == set(TRAIN_STACKS)


def test_canaries_cover_unsloth_stacks() -> None:
    covered = {c.stack for c in TRAIN_CANARIES}
    missing = set(TRAIN_STACKS) - {"stock"} - covered
    assert not missing, f"unsloth stacks with no canary: {missing}"
    assert all(c.stack != "stock" for c in TRAIN_CANARIES)


def test_catalog_unsloth_image_matches_family() -> None:
    data = json.loads(_MODELS_JSON.read_text())
    mismatches = []
    for entry in data["models"]:
        raw = (entry.get("finetuning") or {}).get("unsloth_image")
        if not raw:
            continue
        mid = entry.get("hf_model_id") or entry["id"]
        want = resolve(mid).train_image
        if normalize_train_stack(raw) != want:
            mismatches.append((entry["id"], raw, want))
    assert not mismatches, mismatches


def test_worker_cls_table() -> None:
    assert GPU_CLASS_MAP["L4"] == "L4_vllm"
    assert SERVE_CLASS_MAP[("A100-80GB", "muse_glimmer")] == "A10080GB_muse_glimmer"
    assert worker_cls_name("H200", "muse_glimmer") == "H200_muse_glimmer"
    assert set(GPU_TIER) == {gpu for gpu, image in WORKER_CLS if image == "vllm"}
    for gpu, image in WORKER_CLS:
        assert gpu in GPU_TIER
        assert image in SERVE_STACKS


def test_image_tables_match_stack_ids() -> None:
    from modal_shared.images.serve import SERVE_IMAGES
    from modal_shared.images.train import TRAIN_IMAGES

    assert set(TRAIN_IMAGES) == set(TRAIN_STACKS)
    assert set(SERVE_IMAGES) == set(SERVE_STACKS)
