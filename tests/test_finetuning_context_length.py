"""No context-length literals live in product code — these tests read the same
models.json catalog the code does."""

from __future__ import annotations

import uuid

import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from django.test import override_settings

from overbae.modal.model_registry import (
    get_sft_context_length,
    get_training_context_policy,
    min_sft_context_length,
)
from overbae.services.finetuning_runner import baseten_context_length
from overbae.services.recommendation.analysis import build_analysis
from overbae.services.recommendation.constraints import eligible_models
from overbae.services.recommendation.hyperparams import compute_hyperparams

_POLICY = get_training_context_policy("baseten")
_BUCKETS = sorted(int(b) for b in _POLICY["context_buckets"])
_HEADROOM = int(_POLICY["context_headroom"])


class TestCatalogContextPolicy:
    def test_policy_comes_from_catalog(self):
        assert _BUCKETS, "baseten context buckets must be defined in models.json"
        assert _HEADROOM > 0

    def test_unknown_backend_raises(self):
        with pytest.raises(KeyError):
            get_training_context_policy("no-such-backend")

    def test_baseten_models_are_not_uniformly_capped(self):
        assert get_sft_context_length("Qwen/Qwen3-8B", backend="baseten") == 40960
        assert get_sft_context_length("Qwen/Qwen2.5-7B-Instruct", backend="baseten") == 32768
        assert (
            get_sft_context_length("meta-llama/Llama-3.1-8B-Instruct", backend="baseten") == 131072
        )
        assert get_sft_context_length("Qwen/Qwen3.5-9B", backend="baseten") == 262144
        assert get_sft_context_length("fdtn-ai/antares-1b", backend="baseten") == 131072

    def test_min_sft_context_length(self):
        smallest = min_sft_context_length("baseten")
        assert smallest is not None
        assert smallest >= _BUCKETS[0]


class TestRecommendFiltersByDatasetContext:
    @override_settings(FINETUNING_BACKEND="baseten")
    def test_eligible_models_drop_short_context(self):
        # 50k tokens: Qwen2.5 (32k) and Qwen3 dense (40k) drop; Llama/Qwen3.5 remain.
        filtered, excluded = eligible_models(has_tool_calling=False, max_row_tokens=50_000)
        surviving = {m["id"] for models in filtered.values() for m in models}
        assert surviving, "some models must still fit at 50k"
        assert all(
            (get_sft_context_length(mid, backend="baseten") or 0) >= 50_000 + _HEADROOM
            for mid in surviving
        )
        assert "Qwen/Qwen2.5-7B-Instruct" not in surviving
        assert "Qwen/Qwen3-8B" not in surviving
        assert "meta-llama/Llama-3.1-8B-Instruct" in surviving

        reasons = {e.model: e.reason for e in excluded}
        assert "50,000" in reasons["Qwen/Qwen3-8B"]

    @override_settings(FINETUNING_BACKEND="baseten")
    def test_analysis_excludes_short_context_models(self):
        stats = {
            "num_examples": 100,
            "avg_input_chars": 300,
            "avg_output_chars": 60,
            "has_tool_calling": False,
            "max_token_length": 50_000,
        }
        analysis = build_analysis(stats, task_type="dialogue", task_type_source="heuristic")

        assert analysis["candidates"]
        for row in analysis["candidates"]:
            model_max = get_sft_context_length(row["model"], backend="baseten")
            assert model_max is not None and model_max >= 50_000 + _HEADROOM
        assert any("50,000" in e["reason"] for e in analysis["excluded"])

    @override_settings(FINETUNING_BACKEND="baseten")
    def test_no_candidate_survives_rows_past_every_context_limit(self):
        # Past every baseten SFT max.
        max_tokens = 1_000_000
        eligible, _excluded = eligible_models(has_tool_calling=False, max_row_tokens=max_tokens)
        assert not eligible

        stats = {
            "num_examples": 10,
            "avg_input_chars": 100,
            "avg_output_chars": 20,
            "has_tool_calling": False,
            "max_token_length": max_tokens,
        }
        analysis = build_analysis(stats, task_type="dialogue", task_type_source="heuristic")

        assert analysis["candidates"] == []
        assert analysis["excluded"]
        # A model with no enabled training kind at all is rejected before context is
        # even considered — that reason names no dataset size, unlike every other one.
        assert all(
            "1,000,000" in e["reason"] or e["reason"] == "No supported fine-tuning method"
            for e in analysis["excluded"]
        )


class TestBasetenContextLength:
    def test_default_is_smallest_bucket(self):
        assert baseten_context_length(0) == _BUCKETS[0]
        assert baseten_context_length(100) == _BUCKETS[0]

    def test_snaps_up_never_down(self):
        # A row over a bucket boundary must land in the next bucket — TRL would
        # silently truncate it (corrupting targets) if we snapped down.
        assert baseten_context_length(_BUCKETS[0] + 1) == _BUCKETS[1]
        assert baseten_context_length(11051) == 16384

    def test_headroom_pushes_boundary_rows_up(self):
        assert baseten_context_length(_BUCKETS[1] - _HEADROOM) == _BUCKETS[1]
        assert baseten_context_length(_BUCKETS[1]) == _BUCKETS[2]

    def test_requested_wins_when_larger_than_need(self):
        assert baseten_context_length(100, requested=32768) == 32768

    def test_caps_at_model_max_when_rows_fit(self):
        # Requested context can still be clamped down when the dataset is short.
        assert baseten_context_length(100, model_max=4096, requested=16384) == 4096

    def test_refuses_when_model_cannot_cover_longest_row(self):
        from overbae.services.finetuning_policy import TrainingPlanError

        with pytest.raises(TrainingPlanError, match="refusing to clamp"):
            baseten_context_length(20000, model_max=4096)

    def test_uncapped_request_lands_on_largest_bucket(self):
        assert baseten_context_length(999_999) == _BUCKETS[-1]


class TestRecommenderIncludesContext:
    _ENTRY = {
        "id": "Qwen/Qwen3-8B",
        "total_params_b": 8.2,
        "context_length_sft": 32768,
        "min_batch_size": 1,
        "max_batch_size": 8,
    }

    def test_baseten_hyperparams_include_snapped_context(self):
        with override_settings(FINETUNING_BACKEND="baseten"):
            hp = compute_hyperparams(500, model_entry=self._ENTRY, max_row_tokens=11051)
        assert hp["context_length"] == 16384

    def test_baseten_context_refuses_short_catalog_max(self):
        entry = {**self._ENTRY, "context_length_sft": 4096}
        with (
            override_settings(FINETUNING_BACKEND="baseten"),
            pytest.raises(Exception, match="refusing to clamp|only supports"),
        ):
            compute_hyperparams(500, model_entry=entry, max_row_tokens=11051)

    def test_together_omits_context_length_key(self):
        with override_settings(FINETUNING_BACKEND="together"):
            hp = compute_hyperparams(500, model_entry=self._ENTRY, max_row_tokens=11051)
        assert "context_length" not in hp

    def test_batch_size_respects_model_bounds(self):
        with override_settings(FINETUNING_BACKEND="baseten"):
            hp = compute_hyperparams(500, model_entry=self._ENTRY, max_row_tokens=100)
        assert 1 <= hp["batch_size"] <= 8

    def test_lora_lr_matches_qlora_heuristic(self):
        with override_settings(FINETUNING_BACKEND="baseten"):
            hp = compute_hyperparams(500, model_entry=self._ENTRY, max_row_tokens=100)
        # ≤13B + small dataset → 1e-4 (QLoRA table 9).
        assert hp["learning_rate"] == pytest.approx(1e-4)


@pytest.mark.django_db
class TestJobSerializerContextValidation:
    def _serializer(self, *, max_token_length: int, base_model: str):
        from overbae.api.serializers import FinetuningJobSerializer
        from overbae.models import Project, ProjectMembership, User

        user = User.objects.create_user(
            email=f"ctx-{uuid.uuid4().hex[:8]}@example.com",
            password="pass",
            clerk_user_id=f"clerk_{uuid.uuid4().hex}",
            projects_limit=5,
        )
        project = Project.objects.create(name="ctx", slug=f"ctx-{uuid.uuid4().hex[:8]}")
        ProjectMembership.objects.create(user=user, project=project)
        dataset = frozen_dataset(project, TRAIN_ROWS)
        version = dataset.active_cell
        version.stats = {**version.stats, "max_token_length": max_token_length}
        version.save(update_fields=["stats"])
        # Exact shape the wizard sends (frontend/src/routes/_auth/finetuning.tsx).
        payload = {
            "project": str(project.id),
            "capability": None,
            "dataset": str(dataset.id),
            "eval_dataset": None,
            "eval_set": None,
            "group_id": str(uuid.uuid4()),
            "base_model": base_model,
            "model_tier": "small",
            "name": "ctx-test",
            "hyperparameters": {
                "learning_rate": 1e-4,
                "warmup_ratio": 0.05,
                "training_type": {
                    "type": "Lora",
                    "lora_r": 16,
                    "lora_alpha": 32,
                    "lora_dropout": 0,
                    "lora_trainable_modules": "all-linear",
                },
            },
        }

        class _Req:
            def __init__(self, u):
                self.user = u

        return FinetuningJobSerializer(data=payload, context={"request": _Req(user)})

    @override_settings(FINETUNING_BACKEND="baseten")
    def test_rows_within_model_max_pass(self):
        s = self._serializer(max_token_length=12309, base_model="Qwen/Qwen3-8B")
        assert s.is_valid(), s.errors

    @override_settings(FINETUNING_BACKEND="baseten")
    def test_rows_over_model_max_still_rejected(self):
        model_max = get_sft_context_length("Qwen/Qwen2.5-7B-Instruct", backend="baseten")
        s = self._serializer(max_token_length=model_max + 1, base_model="Qwen/Qwen2.5-7B-Instruct")
        assert not s.is_valid()
        assert "dataset" in s.errors

    @override_settings(FINETUNING_BACKEND="baseten")
    def test_full_ft_rejected_when_catalog_disables_it(self):
        """Gemma 4 31B has full.enabled=false — wizard must not be able to submit Full."""
        s = self._serializer(max_token_length=512, base_model="google/gemma-4-31B-it")
        s.initial_data["hyperparameters"] = {
            "learning_rate": 2e-5,
            "warmup_ratio": 0.05,
            "training_type": {"type": "Full"},
        }
        assert not s.is_valid()
        assert "hyperparameters" in s.errors

    @override_settings(FINETUNING_BACKEND="baseten")
    def test_full_rejected_lora_accepted_between_their_context_lengths(self):
        """LoRA's lighter optimizer state can train at a longer context than full on the
        same model — a dataset row between the two must only be submittable as LoRA."""
        from unittest.mock import patch

        entry = {
            "id": "Qwen/Qwen3-8B",
            "display": "Qwen 3 8B",
            "params": "8B",
            "total_params_b": 8.2,
            "context_length_sft": 131072,
            "max_batch_size": 8,
            "min_batch_size": 1,
            "supports_tool_calling": True,
            "training_type": {
                "full": {"enabled": True, "context_length": 4096, "validated_context_length": True},
                "lora": {
                    "enabled": True,
                    "context_length": 32768,
                    "validated_context_length": True,
                },
            },
        }
        with patch("overbae.services.recommendation.find_catalog_model", return_value=entry):
            lora = self._serializer(max_token_length=10_000, base_model="Qwen/Qwen3-8B")
            assert lora.is_valid(), lora.errors

            full = self._serializer(max_token_length=10_000, base_model="Qwen/Qwen3-8B")
            full.initial_data["hyperparameters"] = {
                "learning_rate": 2e-5,
                "warmup_ratio": 0.05,
                "training_type": {"type": "Full"},
            }
            assert not full.is_valid()
            assert "Switch to LoRA" in full.errors["dataset"][0]
