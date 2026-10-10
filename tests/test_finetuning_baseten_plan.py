from __future__ import annotations

import pytest
from django.test import override_settings

from overbae.services.finetuning_policy import (
    TrainingPlanError,
    default_epochs,
    derive_baseten_training_plan,
    qlora_learning_rate,
    should_pack,
)
from overbae.services.finetuning_runner import ModalRunner
from overbae.services.recommendation.hyperparams import compute_hyperparams


def _plan(**overrides):
    kwargs = dict(
        hyperparameters={},
        num_train_examples=500,
        dataset_stats={"max_token_length": 500},
        params_b=8.0,
        model_max_context=40960,
        model_min_batch=1,
        model_max_batch=8,
        gpu_type="H100",
        gpu_count=1,
    )
    kwargs.update(overrides)
    return derive_baseten_training_plan(**kwargs)


def test_modal_plan_uses_its_catalog_bounds_for_shared_model_id():
    config = ModalRunner.model_config("Qwen/Qwen3.5-2B")
    assert config["backend"] == "baseten"
    plan = _plan(
        hyperparameters={"batch_size": 1},
        model_min_batch=config["finetuning"]["min_batch_size"],
        model_max_batch=config["finetuning"]["max_batch_size"],
    )
    assert plan.batch_size == 1


class TestGapFill:
    """Missing keys are derived from the dataset, never fabricated constants."""

    def test_epochs_from_dataset_size(self):
        plan = _plan(hyperparameters={}, num_train_examples=49)
        assert plan.n_epochs == default_epochs(49)
        assert any("n_epochs" in n for n in plan.notes)

    def test_lr_from_qlora_heuristic(self):
        plan = _plan(params_b=27.0, num_train_examples=500)
        assert plan.learning_rate == qlora_learning_rate(27.0, 500, use_lora=True)

    def test_lora_defaults_scale_with_dataset(self):
        small = _plan(num_train_examples=500)
        large = _plan(num_train_examples=5000)
        assert small.lora_r == 16 and small.lora_dropout == 0.05
        assert large.lora_r == 32 and large.lora_dropout == 0.0
        assert small.lora_alpha == small.lora_r * 2


class TestExplicitValuesWin:
    def test_native_effective_batch_uses_length_budget_instead_of_chat_batch_ceiling(self):
        plan = _plan(
            hyperparameters={
                "objective": "decision_cross_entropy",
                "batch_size": 128,
                "context_length": 32768,
            },
            num_train_examples=1_164_217,
            params_b=4,
            hidden_size=2560,
            model_max_batch=16,
        )
        assert plan.batch_size == 128
        assert plan.per_device_batch * plan.grad_accum == 128
        assert plan.per_device_batch == 64
        assert plan.token_budget == 32768
        env = ModalRunner._training_env(plan, "Qwen/Qwen3.5-4B")
        assert env["PADDED_TOKEN_BUDGET"] == "32768"

    @pytest.mark.parametrize("enabled", [True, False])
    def test_native_baseline_choice_reaches_worker_environment(self, enabled):
        plan = _plan(
            hyperparameters={
                "objective": "decision_cross_entropy",
                "pre_training_baseline": enabled,
            }
        )
        assert ModalRunner._training_env(plan, "Qwen/Qwen3.5-4B")[
            "DECISION_PRE_TRAINING_BASELINE"
        ] == ("1" if enabled else "0")

    def test_experiment_seed_reaches_worker_environment(self):
        plan = _plan(hyperparameters={"seed": 73491})
        assert ModalRunner._training_env(plan, "Qwen/Qwen3-8B")["SEED"] == "73491"

    def test_user_hyperparameters_pass_through_verbatim(self):
        plan = _plan(
            hyperparameters={
                "n_epochs": 5,
                "batch_size": 4,
                "learning_rate": 3e-5,
                "warmup_ratio": 0.1,
                "weight_decay": 0.02,
                "training_type": {
                    "type": "Lora",
                    "lora_r": 8,
                    "lora_alpha": 16,
                    "lora_dropout": 0.0,
                    "lora_trainable_modules": "q_proj,v_proj",
                },
            }
        )
        assert plan.n_epochs == 5
        assert plan.batch_size == 4
        assert plan.learning_rate == 3e-5
        assert plan.warmup_ratio == 0.1
        assert plan.weight_decay == 0.02
        assert (plan.lora_r, plan.lora_alpha, plan.lora_dropout) == (8, 16, 0.0)
        assert plan.lora_target_modules == "q_proj,v_proj"

    @override_settings(FINETUNING_BACKEND="baseten")
    def test_recommender_output_is_reproduced_exactly(self):
        entry = {
            "total_params_b": 8.0,
            "context_length_sft": 40960,
            "min_batch_size": 1,
            "max_batch_size": 8,
        }
        reco = compute_hyperparams(500, model_entry=entry, max_row_tokens=11051)
        plan = _plan(
            hyperparameters=reco,
            num_train_examples=500,
            dataset_stats={"max_token_length": 11051},
        )
        assert plan.n_epochs == reco["n_epochs"]
        assert plan.batch_size == reco["batch_size"]
        assert plan.learning_rate == reco["learning_rate"]
        assert plan.warmup_ratio == reco["warmup_ratio"]
        assert plan.context_length == reco["context_length"]
        tt = reco["training_type"]
        assert plan.lora_r == tt["lora_r"]
        assert plan.lora_alpha == tt["lora_alpha"]
        assert plan.lora_dropout == tt["lora_dropout"]
        assert plan.lora_target_modules == tt["lora_trainable_modules"]


class TestClampsAndBatchMath:
    def test_explicit_batch_above_model_max_is_rejected(self):
        with pytest.raises(TrainingPlanError, match="batch_size"):
            _plan(hyperparameters={"batch_size": 64}, model_max_batch=8)

    def test_batch_never_exceeds_corpus(self):
        with pytest.raises(TrainingPlanError, match="batch_size"):
            _plan(hyperparameters={"batch_size": 8}, num_train_examples=3)

    def test_grad_accum_ignores_gpu_count(self):
        # Training is single-process model-parallel (device_map="auto"), so gpu_count
        # must not divide the effective batch.
        plan = _plan(hyperparameters={"batch_size": 8}, gpu_count=2, params_b=70.0)
        assert plan.per_device_batch == 1
        assert plan.grad_accum == 8
        assert plan.per_device_batch * plan.grad_accum == plan.batch_size


class TestContext:
    def test_context_snapped_from_dataset(self):
        plan = _plan(dataset_stats={"max_token_length": 11051})
        assert plan.context_length == 16384

    def test_requested_context_honoured(self):
        plan = _plan(hyperparameters={"context_length": 32768})
        assert plan.context_length == 32768


class TestGuardrails:
    def test_zero_examples_raises(self):
        with pytest.raises(TrainingPlanError):
            _plan(num_train_examples=0)

    def test_unknown_training_type_raises(self):
        with pytest.raises(TrainingPlanError, match="training_type"):
            _plan(hyperparameters={"training_type": {"type": "Dpo"}})

    def test_rows_over_model_max_raise_instead_of_truncating(self):
        with pytest.raises(TrainingPlanError, match="truncated"):
            _plan(dataset_stats={"max_token_length": 40961}, model_max_context=40960)

    def test_weights_over_vram_raise(self):
        # Even QLoRA (~0.5 B/param) can't fit 200B on 1×H100 (≈100 GB > 68 GB).
        with pytest.raises(TrainingPlanError, match="cannot fit"):
            _plan(params_b=200.0, gpu_type="H100", gpu_count=1)

    def test_70b_qlora_on_one_h100_passes(self):
        # QLoRA path: bf16 140 GB won't fit 1×H100, but 4-bit ≈35 GB does.
        plan = _plan(params_b=70.0, gpu_count=1)
        assert plan.context_length > 0
        assert any("QLoRA" in n for n in plan.notes)


class TestFullFinetuning:
    def test_full_plan_uses_full_ft_lr_heuristic(self):
        plan = _plan(hyperparameters={"training_type": {"type": "Full"}}, params_b=8.0)
        assert plan.training_type == "Full"
        assert plan.learning_rate == qlora_learning_rate(8.0, 500, use_lora=False)

    def test_full_batch_ceiling_is_halved(self):
        lora = _plan(num_train_examples=5000, model_max_batch=8)
        full = _plan(
            hyperparameters={"training_type": {"type": "Full"}},
            num_train_examples=5000,
            model_max_batch=8,
        )
        assert lora.batch_size == 8
        assert full.batch_size == 4

    def test_full_memory_guardrail_is_stricter(self):
        # 32B full FT ≈ 192 GB > 0.85 × 80 GB — must refuse the LoRA GPU pairing.
        with pytest.raises(TrainingPlanError, match="cannot fit"):
            _plan(
                hyperparameters={"training_type": {"type": "Full"}},
                params_b=32.0,
                gpu_type="H100",
                gpu_count=1,
            )
        plan = _plan(
            hyperparameters={"training_type": {"type": "Full"}},
            params_b=32.0,
            gpu_type="H100",
            gpu_count=4,
        )
        assert plan.training_type == "Full"

    def test_default_plan_stays_lora(self):
        assert _plan().training_type == "Lora"


class TestPacking:
    def test_no_pack_small_dataset(self):
        assert not should_pack(200, avg_row_tokens=300, context_length=4096)

    def test_no_pack_long_rows(self):
        assert not should_pack(5000, avg_row_tokens=3000, context_length=4096)

    def test_plan_derives_packing_from_stats(self):
        packed = _plan(
            num_train_examples=5000,
            dataset_stats={
                "max_token_length": 900,
                "avg_input_chars": 600,
                "avg_output_chars": 300,
            },
        )
        unpacked = _plan(
            num_train_examples=50,
            dataset_stats={
                "max_token_length": 900,
                "avg_input_chars": 600,
                "avg_output_chars": 300,
            },
        )
        assert packed.packing is True
        assert unpacked.packing is False

    def test_native_decisions_keep_independent_sequence_boundaries(self):
        plan = _plan(
            hyperparameters={"objective": "decision_cross_entropy", "packing": False},
            num_train_examples=5000,
            dataset_stats={
                "max_token_length": 900,
                "avg_input_chars": 600,
                "avg_output_chars": 300,
            },
        )
        assert plan.packing is False

    def test_gemma4_never_packs(self):
        # flex_attention + GC-off OOMs when packing fills steps to MAX_LENGTH.
        plan = _plan(
            num_train_examples=5000,
            dataset_stats={
                "max_token_length": 900,
                "avg_input_chars": 600,
                "avg_output_chars": 300,
            },
            model_id="google/gemma-4-12B-it",
        )
        assert plan.packing is False
        assert any("packing disabled" in n for n in plan.notes)

    def test_muse_never_packs(self):
        plan = _plan(
            num_train_examples=5000,
            dataset_stats={
                "max_token_length": 900,
                "avg_input_chars": 600,
                "avg_output_chars": 300,
            },
            model_id="unsloth/Muse-Glimmer-30B",
        )
        assert plan.packing is False
        assert any("packing disabled" in n for n in plan.notes)

    def test_nemotron35_never_packs(self):
        plan = _plan(
            num_train_examples=5000,
            dataset_stats={
                "max_token_length": 900,
                "avg_input_chars": 600,
                "avg_output_chars": 300,
            },
            model_id="nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B",
        )
        assert plan.packing is False
        assert any("packing disabled" in n for n in plan.notes)

    def test_gemma4_moe_rejects_requested_nonzero_dropout(self):
        with pytest.raises(TrainingPlanError, match="lora_dropout"):
            _plan(
                hyperparameters={"training_type": {"type": "Lora", "lora_dropout": 0.05}},
                params_b=25.2,
                model_id="google/gemma-4-26B-A4B-it",
            )
