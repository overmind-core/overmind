from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from conftest import EVAL_ROWS, frozen_dataset
from django.urls import reverse
from factories import auth_client, make_capability, make_project, make_user, prepare_training
from rest_framework.test import APIClient

from overbae.modal.model_registry import baseten_finetuning_catalog
from overbae.models import (
    Capability,
    Dataset,
    EvalSet,
    EvalSetMember,
    Evaluator,
    FinetuningJob,
    FinetuningJobEvent,
    Project,
    ProjectMembership,
    User,
)
from overbae.services.datasets.rows import row as _dataset_row
from overbae.services.datasets.rows import row_from_record
from overbae.services.finetuning_runner import (
    BasetenRunner,
    TogetherAIRunner,
    get_runner,
    together_suffix,
)
from overbae.services.finetuning_tool_validation import check_tool_calling_rows
from overbae.services.finetuning_validator import (
    validate_dataset,
    validate_rows,
)
from overbae.services.recommendation.hyperparams import (
    compute_hyperparams,
    hyperparam_provenance,
)

pytestmark = pytest.mark.django_db

CELERY_PATH = "overbae.tasks.finetuning.run_finetuning.apply_async"


def _dataset_with_messages(
    capability: Capability, *, n: int = 3, prefix: str = "Question"
) -> Dataset:
    ds = frozen_dataset(
        capability.project,
        [
            {
                "messages": [
                    {"role": "user", "content": f"{prefix} {i}"},
                    {"role": "assistant", "content": f"Answer {i}"},
                ]
            }
            for i in range(n)
        ],
        capability=capability,
    )
    return ds


def _dataset_with_pairs(capability: Capability, *, n: int = 3) -> Dataset:
    ds = frozen_dataset(
        capability.project,
        [{"input": f"input {i}", "expected_output": f"output {i}"} for i in range(n)],
        capability=capability,
    )
    return ds


def _ft_job_payload(project: Project, dataset: Dataset, **overrides) -> dict:
    evaluation = frozen_dataset(project, EVAL_ROWS)
    eval_set = EvalSet.objects.create(project=project, name="Training evals")
    evaluator = Evaluator.objects.create(
        project=project,
        name="Exact match",
        kind=Evaluator.Kind.DETERMINISTIC,
        config={"check": "exact_match"},
    )
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=evaluator, role=EvalSetMember.Role.GENERATIVE
    )
    payload = {
        "project": str(project.id),
        "dataset": str(dataset.id),
        "eval_dataset": str(evaluation.id),
        "eval_set": str(eval_set.id),
        "name": "ft-test",
        "use_case": "test run",
        "base_model": "meta-llama/Llama-3.2-3B-Instruct",
        "hyperparameters": {"learning_rate": 3e-4, "epochs": 2},
    }
    payload.update(overrides)
    return payload


def _setup() -> tuple[User, Project, Capability]:
    u = make_user()
    p = make_project()
    ProjectMembership.objects.create(user=u, project=p)
    a = make_capability(p)
    return u, p, a


class _FakeAsyncResult:
    def __init__(self, task_id: str = "task-id"):
        self.id = task_id


class TestToolCallingValidation:
    def _valid_tool_row(self) -> dict:
        return {
            "messages": [
                {"role": "user", "content": "weather?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_abc",
                    "name": "get_weather",
                    "content": "{}",
                },
                {"role": "assistant", "content": "72F"},
            ]
        }

    def test_valid_tool_row_passes(self):
        result = check_tool_calling_rows([self._valid_tool_row()])
        assert result.issue_count == 0
        assert result.errors == []

    def test_extra_tool_responses_fails(self):
        row = self._valid_tool_row()
        row["messages"].insert(
            3,
            {"role": "tool", "tool_call_id": "call_extra", "content": "{}"},
        )
        result = check_tool_calling_rows([row])
        assert result.issue_count >= 1
        assert any("tool response" in e.lower() for e in result.errors)

    def test_tool_name_must_match_tools_list(self):
        row = self._valid_tool_row()
        row["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "weather",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        row["messages"][2]["name"] = "wrong_name"
        result = check_tool_calling_rows([row])
        assert result.issue_count >= 1
        assert any("tools list" in e for e in result.errors)


class TestValidatorDB:
    def test_instruction_pairs_dataset_is_invalid(self):
        # Fine-tuning rows are native {messages, tools?}; a prompt/completion pair
        # must fail explicitly rather than be reshaped.
        _, p, a = _setup()
        ds = _dataset_with_pairs(a, n=4)
        result = validate_dataset(str(ds.id))
        assert result.valid is False
        assert result.errors

    def test_eval_rows_are_not_training_rows(self):
        _, p, a = _setup()
        ds = frozen_dataset(a.project, EVAL_ROWS, capability=a)
        result = validate_dataset(str(ds.id))
        assert result.valid is False
        assert any("messages is empty" in e for e in result.errors)

    def test_nonexistent_dataset_returns_error(self):
        result = validate_dataset(str(uuid.uuid4()))
        assert result.valid is False
        assert any("not found" in e.lower() for e in result.errors)

    def test_tool_calling_mismatch_dataset_invalid(self):
        _, _, a = _setup()
        ds = frozen_dataset(
            a.project,
            [
                {
                    "messages": [
                        {"role": "user", "content": "go"},
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_real",
                                    "type": "function",
                                    "function": {"name": "fn", "arguments": "{}"},
                                }
                            ],
                        },
                        {"role": "tool", "tool_call_id": "call_bad", "content": "{}"},
                        {"role": "assistant", "content": "done"},
                    ]
                }
                for i in range(12)
            ],
            capability=a,
        )
        result = validate_dataset(str(ds.id))
        assert result.valid is False
        assert any("does not match" in e for e in result.errors)
        assert result.stats.get("tool_calling_issues", 0) >= 1


class TestRecommenderLogic:
    pytestmark = pytest.mark.django_db(transaction=False)

    def test_hyperparams_small_dataset(self, settings):
        settings.FINETUNING_BACKEND = "together"
        hp = compute_hyperparams(50, use_lora=True)
        assert hp["n_epochs"] == 2  # 100 // 50
        # OpenAI 0.2%-of-examples rule: round(0.002 * 50) → 0, floored to 1
        assert hp["batch_size"] == 1
        assert hp["learning_rate"] == 1e-4
        assert "training_type" in hp

    def test_hyperparams_medium_dataset(self, settings):
        settings.FINETUNING_BACKEND = "together"
        hp = compute_hyperparams(500, use_lora=True)
        assert hp["n_epochs"] == 3
        assert hp["batch_size"] == 1  # round(0.002 * 500) == 1

    def test_hyperparams_large_dataset(self):
        hp = compute_hyperparams(5000, use_lora=True)
        assert hp["n_epochs"] == 3
        assert hp["batch_size"] == 8  # round(10), capped at default max_batch_size 8

    def test_hyperparams_uses_model_entry_max_batch(self):
        model_entry = {"total_params_b": 27.0, "max_batch_size": 16}
        hp = compute_hyperparams(10000, model_entry=model_entry, use_lora=True)
        assert hp["batch_size"] == 16  # round(20), capped at model max

    def test_hyperparams_full_ft_has_no_training_type(self):
        hp = compute_hyperparams(100, use_lora=False)
        assert "training_type" not in hp
        assert hp["learning_rate"] == 1e-5

    def test_hyperparams_floor_one_epoch(self):
        hp = compute_hyperparams(1, use_lora=True)
        assert hp["n_epochs"] >= 1

    def test_hyperparams_use_model_aware_lr(self):
        hp = compute_hyperparams(
            500,
            model_entry={"total_params_b": 27.0},
            use_lora=True,
        )
        assert hp["learning_rate"] == 5e-5

    def test_hyperparam_provenance_covers_stamped_knobs(self, settings):
        settings.FINETUNING_BACKEND = "baseten"
        hp = compute_hyperparams(49, model_entry={"total_params_b": 8.0}, use_lora=True)
        reasons = hyperparam_provenance(
            hp, num_examples=49, params_b=8.0, use_lora=True, backend="baseten"
        )
        assert {"n_epochs", "learning_rate", "batch_size", "warmup_ratio", "lora_r"} <= set(reasons)
        assert "49 examples" in reasons["n_epochs"]
        assert all(isinstance(v, str) and v for v in reasons.values())

    def test_baseten_catalog_includes_batch_size_limits(self):
        # Model-agnostic: any entry can be disabled in models.json at any time.
        entries = [m for tier in baseten_finetuning_catalog().values() for m in tier]
        assert entries
        for m in entries:
            assert isinstance(m["min_batch_size"], int), m["id"]
            assert isinstance(m["max_batch_size"], int), m["id"]
            assert 1 <= m["min_batch_size"] <= m["max_batch_size"], m["id"]


class TestRunnerAbstraction:
    def test_get_runner_returns_together_runner_by_default(self, settings):
        settings.FINETUNING_BACKEND = "together"
        runner = get_runner()
        assert isinstance(runner, TogetherAIRunner)

    def test_get_runner_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown fine-tuning backend"):
            get_runner("nonexistent_backend")

    def test_together_suffix_strips_unsafe_characters(self):
        assert (
            together_suffix(
                "huge rhinoceros · Qwen 2.5 7B Instruct",
                fallback="ft-deadbeef",
            )
            == "huge-rhinoceros-qwen-2-5-7b-instruct"
        )
        assert together_suffix(None, fallback="ft-abc12345") == "ft-abc12345"
        assert together_suffix("   ", fallback="ft-fallback1") == "ft-fallback1"

    def test_together_runner_submit_uploads_and_creates_job(self):
        runner = TogetherAIRunner()

        job = SimpleNamespace(
            id=uuid.uuid4(),
            base_model="meta-llama/Llama-3.2-3B-Instruct",
            name="smoke-test",
            hyperparameters={
                "n_epochs": 2,
                "learning_rate": 1e-4,
                "training_type": {
                    "type": "Lora",
                    "lora_r": 8,
                    "lora_alpha": 16,
                    "lora_dropout": 0.0,
                    "lora_trainable_modules": "all-linear",
                },
            },
        )

        mock_client = MagicMock()
        mock_client.files.upload.return_value = SimpleNamespace(id="file-123")
        mock_client.fine_tuning.create.return_value = SimpleNamespace(id="ft-job-456")

        with patch.object(runner, "_client", return_value=mock_client):
            result = runner.submit(
                job=job,
                training_file_path="/tmp/fake.jsonl",
                num_examples=50,
            )

        assert result.remote_id == "ft-job-456"
        assert result.training_file_id == "file-123"
        assert result.num_examples == 50
        assert "ft-job-456" in result.run_url
        mock_client.files.upload.assert_called_once_with(
            file="/tmp/fake.jsonl", purpose="fine-tune"
        )
        assert mock_client.fine_tuning.create.call_args.kwargs["suffix"] == "smoke-test"

    def test_together_runner_submit_sanitizes_display_name_suffix(self):
        runner = TogetherAIRunner()
        job = SimpleNamespace(
            id=uuid.uuid4(),
            base_model="Qwen/Qwen2.5-7B-Instruct",
            name="huge rhinoceros · Qwen 2.5 7B Instruct",
            hyperparameters={"n_epochs": 1},
        )
        mock_client = MagicMock()
        mock_client.files.upload.return_value = SimpleNamespace(id="file-123")
        mock_client.fine_tuning.create.return_value = SimpleNamespace(id="ft-job-789")

        with patch.object(runner, "_client", return_value=mock_client):
            runner.submit(job=job, training_file_path="/tmp/fake.jsonl", num_examples=10)

        assert (
            mock_client.fine_tuning.create.call_args.kwargs["suffix"]
            == "huge-rhinoceros-qwen-2-5-7b-instruct"
        )

    def test_together_runner_submit_reuses_existing_file(self):
        runner = TogetherAIRunner()

        job = SimpleNamespace(
            id=uuid.uuid4(),
            base_model="meta-llama/Llama-3.2-3B-Instruct",
            name="reuse-test",
            hyperparameters={"n_epochs": 1},
        )

        mock_client = MagicMock()
        mock_client.fine_tuning.create.return_value = SimpleNamespace(id="ft-reuse-789")

        with patch.object(runner, "_client", return_value=mock_client):
            result = runner.submit(
                job=job,
                training_file_path="",
                num_examples=None,
                existing_file_id="file-already-uploaded",
            )

        mock_client.files.upload.assert_not_called()
        assert result.training_file_id == "file-already-uploaded"
        assert result.remote_id == "ft-reuse-789"
        assert result.num_examples is None

    def test_together_runner_poll_parses_response(self):
        """Loss comes from event messages; step from the step field on each event."""
        runner = TogetherAIRunner()
        mock_client = MagicMock()

        fake_ft = SimpleNamespace(
            status="running",
            epochs_completed=1,
            token_count=10000,
            x_model_output_name=None,
            x_model_output_path=None,
            events=[
                # Chronological order: oldest first (Together AI's natural order)
                SimpleNamespace(
                    step=38,
                    type="checkpoint_save",
                    message="Step 38 train_loss=0.61",
                ),
                SimpleNamespace(
                    step=42,
                    type="checkpoint_save",
                    message="Step 42 train_loss=0.5432 eval_loss=0.6123",
                ),
            ],
        )
        mock_client.fine_tuning.retrieve.return_value = fake_ft

        with patch.object(runner, "_client", return_value=mock_client):
            snap = runner.poll("ft-job-abc")

        assert snap.state == "running"
        assert snap.train_loss == pytest.approx(0.5432)
        assert snap.eval_loss == pytest.approx(0.6123)
        assert snap.step == 42  # highest step from events

    def test_together_runner_poll_completed_state(self):
        """x_model_output_name / x_model_output_path are the SDK's Pydantic field names."""
        runner = TogetherAIRunner()
        mock_client = MagicMock()

        fake_ft = SimpleNamespace(
            status="completed",
            epochs_completed=3,
            token_count=50000,
            x_model_output_name="myspace/llama-ft-abc",
            x_model_output_path="https://weights.example.com/ft.bin",
            events=[],
        )
        mock_client.fine_tuning.retrieve.return_value = fake_ft

        with patch.object(runner, "_client", return_value=mock_client):
            snap = runner.poll("ft-job-done")

        assert runner.is_terminal_ok(snap.state)
        assert snap.output_model_name == "myspace/llama-ft-abc"
        assert snap.weights_url == "https://weights.example.com/ft.bin"

    def test_terminal_state_classification(self):
        runner = BasetenRunner()
        assert runner.is_terminal_ok("succeeded")
        assert not runner.is_terminal_ok("running")
        assert runner.is_terminal_fail("failed")
        assert not runner.is_terminal_fail("succeeded")
        assert runner.is_terminal_cancelled("cancelled")
        assert not runner.is_terminal_cancelled("running")


class TestNewModelFields:
    def test_group_id_and_model_tier_exposed_in_list_api(self):
        u, p, a = _setup()
        ds = _dataset_with_pairs(a)
        gid = uuid.uuid4()
        FinetuningJob.objects.create(
            project=p,
            dataset=ds,
            base_model="m",
            group_id=gid,
            model_tier=FinetuningJob.Tier.MID,
        )

        with patch(CELERY_PATH):
            r = auth_client(u).get(reverse("finetuningjob-list"))

        assert r.status_code == 200
        row = r.data["results"][0]
        assert str(row["group_id"]) == str(gid)
        assert row["model_tier"] == "mid"


class TestValidateDatasetEndpoint:
    def test_valid_dataset_returns_valid_true(self):
        u, p, a = _setup()
        ds = _dataset_with_messages(a, n=5)

        r = auth_client(u).post(
            reverse("finetuningjob-validate-dataset"),
            {"dataset_id": str(ds.id)},
            format="json",
        )
        assert r.status_code == 200
        assert r.data["valid"] is True
        assert r.data["format"] == "conversational"
        assert r.data["num_examples"] == 5

    def test_empty_dataset_returns_valid_false(self):
        u, p, a = _setup()
        ds = frozen_dataset(a.project, EVAL_ROWS, capability=a)

        r = auth_client(u).post(
            reverse("finetuningjob-validate-dataset"),
            {"dataset_id": str(ds.id)},
            format="json",
        )
        assert r.status_code == 200
        assert r.data["valid"] is False

    def test_missing_dataset_id_returns_400(self):
        u, _, _ = _setup()
        r = auth_client(u).post(
            reverse("finetuningjob-validate-dataset"),
            {},
            format="json",
        )
        assert r.status_code == 400

    def test_unauthenticated_returns_401(self):
        r = APIClient().post(
            reverse("finetuningjob-validate-dataset"),
            {"dataset_id": str(uuid.uuid4())},
            format="json",
        )
        assert r.status_code == 401

    def test_validate_dataset_preview_separate_validation_dataset(self):
        u, p, a = _setup()
        train_ds = _dataset_with_messages(a, n=8)
        val_ds = _dataset_with_messages(a, n=3, prefix="Held out")

        r = auth_client(u).post(
            reverse("finetuningjob-validate-dataset"),
            {
                "dataset_id": str(train_ds.id),
                "validation_enabled": True,
                "validation_dataset_id": str(val_ds.id),
            },
            format="json",
        )
        assert r.status_code == 200
        stats = r.data["stats"]
        assert stats["validation_mode"] == "separate"
        assert stats["train_examples"] == 8
        assert stats["val_examples"] == 3

    def test_validate_dataset_preview_validation_disabled(self):
        u, p, a = _setup()
        ds = _dataset_with_messages(a, n=6)

        r = auth_client(u).post(
            reverse("finetuningjob-validate-dataset"),
            {
                "dataset_id": str(ds.id),
                "validation_enabled": False,
            },
            format="json",
        )
        assert r.status_code == 200
        stats = r.data["stats"]
        assert stats["validation_mode"] == "off"
        assert stats["train_examples"] == 6
        assert stats["val_examples"] == 0


class TestModelsEndpoint:
    def test_models_returns_backend(self, settings):
        u, _, _ = _setup()
        settings.FINETUNING_BACKEND = "together"

        r = auth_client(u).get(reverse("finetuningjob-models"))
        assert r.status_code == 200
        assert r.data["backend"] == "together"
        assert "tiers" in r.data
        assert "models" in r.data

    def test_models_returns_baseten_backend(self, settings):
        u, _, _ = _setup()
        settings.FINETUNING_BACKEND = "baseten"

        r = auth_client(u).get(reverse("finetuningjob-models"))
        assert r.status_code == 200
        assert r.data["backend"] == "baseten"
        assert set(r.data["tiers"]) == {"compact", "small", "mid", "large"}


class TestRecommendEndpoint:
    def test_recommend_returns_ranked_candidates(self):
        u, p, a = _setup()
        ds = _dataset_with_pairs(a, n=10)

        r = auth_client(u).post(
            reverse("finetuningjob-recommend"),
            {"dataset_id": str(ds.id)},
            format="json",
        )
        assert r.status_code == 200
        assert r.data["task_type"]
        assert r.data["task_type_source"] in {"semantic", "heuristic"}
        assert r.data["skill_weights"]
        assert len(r.data["candidates"]) >= 2
        assert 1 <= len(r.data["shown"]) <= 3

    def test_recommend_includes_provenance(self, settings):
        settings.FINETUNING_BACKEND = "baseten"
        u, p, a = _setup()
        ds = _dataset_with_pairs(a, n=10)

        r = auth_client(u).post(
            reverse("finetuningjob-recommend"),
            {"dataset_id": str(ds.id)},
            format="json",
        )
        assert r.status_code == 200
        for rec in r.data["candidates"]:
            assert rec["hyperparam_reasons"].get("n_epochs")

    def test_recommend_dates_the_snapshot_and_cites_every_benchmark(self):
        u, p, a = _setup()
        ds = _dataset_with_pairs(a, n=5)

        r = auth_client(u).post(
            reverse("finetuningjob-recommend"),
            {"dataset_id": str(ds.id)},
            format="json",
        )
        assert r.status_code == 200
        assert r.data["benchmark_snapshot"]["generated_at"]
        rows = [row for c in r.data["candidates"] for row in c["evidence"]]
        assert rows
        assert all(row["source"] and row["url"].startswith("https://") for row in rows)

    def test_foreign_dataset_returns_404(self):
        u, _, _ = _setup()
        _, p2, a2 = _setup()
        foreign_ds = _dataset_with_pairs(a2, n=3)

        r = auth_client(u).post(
            reverse("finetuningjob-recommend"),
            {"dataset_id": str(foreign_ds.id)},
            format="json",
        )
        assert r.status_code == 404

    def test_missing_dataset_id_returns_400(self):
        u, _, _ = _setup()
        r = auth_client(u).post(
            reverse("finetuningjob-recommend"),
            {},
            format="json",
        )
        assert r.status_code == 400


class TestLossCurvesEndpoint:
    def test_empty_job_returns_empty_curves(self):
        u, p, a = _setup()
        ds = _dataset_with_pairs(a)
        job = FinetuningJob.objects.create(project=p, dataset=ds, base_model="m")

        r = auth_client(u).get(reverse("finetuningjob-loss-curves", kwargs={"id": str(job.id)}))
        assert r.status_code == 200
        assert r.data["steps"] == []
        assert r.data["train_loss"] == []
        assert r.data["eval_loss"] == []

    def test_returns_loss_points_from_progress_events(self):
        u, p, a = _setup()
        ds = _dataset_with_pairs(a)
        job = FinetuningJob.objects.create(
            project=p, dataset=ds, base_model="m", status=FinetuningJob.Status.RUNNING
        )
        FinetuningJobEvent.objects.create(
            job=job,
            event_type="progress",
            data={"state": "running", "step": 10, "train_loss": 2.1, "eval_loss": 2.3},
        )
        FinetuningJobEvent.objects.create(
            job=job,
            event_type="progress",
            data={"state": "running", "step": 20, "train_loss": 1.5, "eval_loss": 1.8},
        )
        FinetuningJobEvent.objects.create(
            job=job,
            event_type="status_change",
            data={"status": "running"},  # should not appear in loss curves
        )

        r = auth_client(u).get(reverse("finetuningjob-loss-curves", kwargs={"id": str(job.id)}))
        assert r.status_code == 200
        assert len(r.data["steps"]) == 2
        assert len(r.data["train_loss"]) == 2
        train_losses = r.data["train_loss"]
        assert all(v is not None for v in train_losses)

    def test_loss_curves_404_for_foreign_job(self):
        u, _, _ = _setup()
        _, p2, a2 = _setup()
        ds2 = _dataset_with_pairs(a2)
        foreign = FinetuningJob.objects.create(project=p2, dataset=ds2, base_model="m")

        r = auth_client(u).get(reverse("finetuningjob-loss-curves", kwargs={"id": str(foreign.id)}))
        assert r.status_code == 404

    def test_unauthenticated_returns_401(self):
        u, p, a = _setup()
        ds = _dataset_with_pairs(a)
        job = FinetuningJob.objects.create(project=p, dataset=ds, base_model="m")

        r = APIClient().get(reverse("finetuningjob-loss-curves", kwargs={"id": str(job.id)}))
        assert r.status_code == 401


class TestJSONLMaterialisation:
    pytestmark = pytest.mark.django_db(transaction=False)

    def test_messages_passthrough(self):
        from overbae.services.finetuning_validator import row_to_finetuning_line

        msgs = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        }
        dp = row_from_record(0, msgs)
        assert row_to_finetuning_line(dp) == msgs

    def test_messages_with_tools_passthrough(self):
        from overbae.services.finetuning_validator import row_to_finetuning_line

        row = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
            "tools": [{"type": "function", "function": {"name": "f"}}],
        }
        dp = row_from_record(0, row)
        assert row_to_finetuning_line(dp) == row

    def test_non_native_input_raises(self):
        from overbae.services.finetuning_validator import row_to_finetuning_line

        # None of these are fine-tuning-native — explicit error, never a silent drop.
        for bad in (
            row_from_record(0, {"input": "Q", "expected_output": "A"}),
            row_from_record(0, {"input": "Q"}),
            row_from_record(0, {"input": {"key": "val"}, "expected_output": "out"}),
            row_from_record(0, {"messages": [{"role": "user", "content": "hi"}]}),
        ):
            with pytest.raises(ValueError):
                row_to_finetuning_line(bad)


class TestOpenAIFormatValidator:
    pytestmark = pytest.mark.django_db(transaction=False)

    def _conv(self, messages, **kwargs) -> dict:
        row = {"messages": messages}
        row.update(kwargs)
        return row

    def _single_turn(self, user="Hello", assistant="Hi!") -> dict:
        return self._conv(
            [
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            ]
        )

    def _multi_turn(self) -> dict:
        return self._conv(
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Q1"},
                {"role": "assistant", "content": "A1"},
                {"role": "user", "content": "Q2"},
                {"role": "assistant", "content": "A2"},
            ]
        )

    def _tool_row(self) -> dict:
        return {
            "messages": [
                {"role": "user", "content": "What's the weather?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_x1",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": '{"city": "Tokyo"}'},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_x1",
                    "name": "get_weather",
                    "content": '{"temp": 22}',
                },
                {"role": "assistant", "content": "It is 22°C in Tokyo."},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                        },
                    },
                }
            ],
        }

    def _rows(self, row, n=12):
        return [row] * n

    def test_invalid_role_rejected(self):
        row = self._conv(
            [
                {"role": "user", "content": "hi"},
                {"role": "bot", "content": "hey"},
            ]
        )
        result = validate_rows(self._rows(row))
        assert result.valid is False
        assert any("invalid role" in e.lower() for e in result.errors)

    def test_missing_assistant_rejected(self):
        row = self._conv(
            [
                {"role": "user", "content": "hi"},
                {"role": "user", "content": "hello again"},
            ]
        )
        result = validate_rows(self._rows(row))
        assert result.valid is False
        assert any("assistant" in e for e in result.errors)

    def test_must_end_with_assistant(self):
        row = self._conv(
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "thanks"},
            ]
        )
        result = validate_rows(self._rows(row))
        assert result.valid is False
        assert any("end with" in e.lower() for e in result.errors)

    def test_too_few_messages_rejected(self):
        row = self._conv([{"role": "assistant", "content": "hi"}])
        result = validate_rows(self._rows(row))
        assert result.valid is False

    def test_assistant_with_no_content_or_tool_calls_rejected(self):
        row = self._conv(
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": None},  # no tool_calls either
            ]
        )
        result = validate_rows(self._rows(row))
        assert result.valid is False
        assert any("content" in e or "tool_calls" in e for e in result.errors)

    def test_tool_call_missing_id_rejected(self):
        row = self._tool_row()
        del row["messages"][1]["tool_calls"][0]["id"]
        result = validate_rows(self._rows(row))
        assert result.valid is False
        assert any("id" in e for e in result.errors)

    def test_tool_call_wrong_type_rejected(self):
        row = self._tool_row()
        row["messages"][1]["tool_calls"][0]["type"] = "action"
        result = validate_rows(self._rows(row))
        assert result.valid is False
        assert any("type" in e and "function" in e for e in result.errors)

    def test_tool_call_arguments_not_string_rejected(self):
        row = self._tool_row()
        row["messages"][1]["tool_calls"][0]["function"]["arguments"] = {"city": "Tokyo"}
        result = validate_rows(self._rows(row))
        assert result.valid is False
        assert any("arguments" in e for e in result.errors)

    def test_tool_call_arguments_invalid_json_rejected(self):
        row = self._tool_row()
        row["messages"][1]["tool_calls"][0]["function"]["arguments"] = "{bad json"
        result = validate_rows(self._rows(row))
        assert result.valid is False
        assert any("arguments" in e for e in result.errors)

    def test_tool_message_missing_tool_call_id_rejected(self):
        row = self._tool_row()
        del row["messages"][2]["tool_call_id"]
        result = validate_rows(self._rows(row))
        assert result.valid is False
        assert any("tool_call_id" in e for e in result.errors)

    def test_tool_message_content_must_be_string(self):
        row = self._tool_row()
        row["messages"][2]["content"] = {"temp": 22}
        result = validate_rows(self._rows(row))
        assert result.valid is False
        assert any("string" in e.lower() for e in result.errors)

    def test_unknown_message_key_flagged(self):
        row = self._conv(
            [
                {"role": "user", "content": "hi", "solution": "extra field"},
                {"role": "assistant", "content": "hey"},
            ]
        )
        result = validate_rows(self._rows(row))
        assert result.valid is False
        assert any("solution" in e for e in result.errors)

    def test_valid_instruction_rows(self):
        rows = [{"prompt": f"Q{i}", "completion": f"A{i}"} for i in range(15)]
        result = validate_rows(rows)
        assert result.valid is True
        assert result.format == "instruction"
        assert result.errors == []

    def test_instruction_empty_prompt_rejected(self):
        rows = [{"prompt": "", "completion": "A"}] * 12
        result = validate_rows(rows)
        assert result.valid is False
        assert any("prompt" in e for e in result.errors)

    def test_instruction_missing_completion_rejected(self):
        rows = [{"prompt": "Q", "completion": ""}] * 12
        result = validate_rows(rows)
        assert result.valid is False
        assert any("completion" in e for e in result.errors)

    def test_fewer_than_10_examples_warns(self):
        rows = [self._single_turn()] * 5
        result = validate_rows(rows)
        assert result.valid is True
        assert any("10" in w for w in result.warnings)

    def test_empty_dataset_invalid(self):
        result = validate_rows([])
        assert result.valid is False
        assert result.num_examples == 0

    def test_unknown_format_rejected(self):
        rows = [{"foo": "bar", "baz": 1}] * 12
        result = validate_rows(rows)
        assert result.valid is False
        assert any("unrecognised format" in e.lower() for e in result.errors)

    def test_validate_rows_uses_openai_check(self):

        bad_row = self._conv(
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": None},  # no tool_calls
            ]
        )
        result = validate_rows([bad_row] * 12)
        assert result.valid is False

    def test_validate_rows_valid_single_turn(self):

        result = validate_rows(self._rows(self._single_turn()))
        assert result.valid is True

    def test_validate_rows_valid_multi_turn(self):

        result = validate_rows(self._rows(self._multi_turn()))
        assert result.valid is True

    def test_validate_rows_valid_tool_calling(self):

        result = validate_rows(self._rows(self._tool_row()))
        assert result.valid is True

    def test_empty_messages_list_rejected(self):
        row = self._conv([])
        result = validate_rows(self._rows(row))
        assert result.valid is False
        assert any("empty" in e.lower() or "0" in e for e in result.errors)

    def test_invalid_role_does_not_produce_spurious_end_check_error(self):
        from overbae.services.finetuning_validator import _validate_conversational_row

        row = self._conv(
            [
                {"role": "user", "content": "hi"},
                {"role": "bad_role", "content": "hey"},
            ]
        )
        issues = _validate_conversational_row("Ex 1", row)
        role_errors = [e for e in issues if "bad_role" in e]
        end_errors = [e for e in issues if "end with" in e.lower()]
        assert len(role_errors) == 1, "exactly one 'invalid role' error expected"
        assert len(end_errors) == 0, "no spurious 'must end with assistant' error expected"

    def test_non_native_input_rejected(self):

        from overbae.services.finetuning_validator import row_to_finetuning_line

        dp = row_from_record(0, {"messages": "not a list"})
        with pytest.raises(ValueError):
            row_to_finetuning_line(dp)

    def test_user_only_messages_rejected(self):

        from overbae.services.finetuning_validator import row_to_finetuning_line

        dp = row_from_record(0, {"messages": [{"role": "user", "content": "hello"}]})
        with pytest.raises(ValueError):
            row_to_finetuning_line(dp)

    def test_no_assistant_no_expected_output_row_invalid(self):
        row = self._conv([{"role": "user", "content": "hello"}])
        result = validate_rows(self._rows(row))
        assert result.valid is False
        assert any("assistant" in e for e in result.errors)

    def test_too_few_messages_reports_single_error_not_double(self):
        from overbae.services.finetuning_validator import _validate_conversational_row

        row = self._conv([{"role": "assistant", "content": "hi"}])
        issues = _validate_conversational_row("Ex 1", row)
        length_errors = [e for e in issues if "at least 2" in e]
        missing_assistant_errors = [e for e in issues if "at least one" in e]
        assert len(length_errors) == 1
        # "has_assistant" IS true here — no spurious "missing assistant" error
        assert len(missing_assistant_errors) == 0


class TestFinetuningValidationSerializer:
    def test_serializer_rejects_eval_validation_dataset(self):
        u, p, a = _setup()
        train_ds = _dataset_with_messages(a, n=5)
        eval_ds = frozen_dataset(p, EVAL_ROWS, capability=a)

        with patch(CELERY_PATH):
            r = auth_client(u).post(
                reverse("finetuningjob-list"),
                _ft_job_payload(
                    p,
                    train_ds,
                    validation_enabled=True,
                    validation_dataset=str(eval_ds.id),
                ),
                format="json",
            )

        assert r.status_code == 400
        assert "train" in str(r.json()["validation_dataset"]).lower()

    def test_serializer_rejects_invalid_validation_split_ratio(self):
        u, p, a = _setup()
        ds = _dataset_with_messages(a, n=5)

        with patch(CELERY_PATH):
            r = auth_client(u).post(
                reverse("finetuningjob-list"),
                _ft_job_payload(p, ds, validation_split_ratio=0.9),
                format="json",
            )

        assert r.status_code == 400
        assert "validation_split_ratio" in r.json()

    def test_serializer_rejects_same_training_and_validation_dataset(self):
        u, p, a = _setup()
        ds = _dataset_with_messages(a, n=5)

        with patch(CELERY_PATH):
            r = auth_client(u).post(
                reverse("finetuningjob-list"),
                _ft_job_payload(
                    p,
                    ds,
                    validation_enabled=True,
                    validation_dataset=str(ds.id),
                ),
                format="json",
            )

        assert r.status_code == 400
        assert "differ" in str(r.json()["validation_dataset"]).lower()

    def test_serializer_rejects_stratified_split_method(self):
        u, p, a = _setup()
        ds = _dataset_with_pairs(a, n=10)

        with patch(CELERY_PATH):
            r = auth_client(u).post(
                reverse("finetuningjob-list"),
                _ft_job_payload(
                    p,
                    ds,
                    validation_enabled=True,
                    validation_split_ratio=0.2,
                    split_method="stratified",
                ),
                format="json",
            )

        assert r.status_code == 400
        assert "split_method" in r.json()

    def test_serializer_accepts_valid_holdout_config(self):
        u, p, a = _setup()
        ds = _dataset_with_messages(a, n=10)

        with patch(CELERY_PATH, return_value=_FakeAsyncResult()):
            r = auth_client(u).post(
                reverse("finetuningjob-list"),
                _ft_job_payload(
                    p,
                    ds,
                    validation_enabled=True,
                    validation_split_ratio=0.2,
                    split_method="random",
                ),
                format="json",
            )

        assert r.status_code == 201, r.data
        job = FinetuningJob.objects.get(pk=r.data["id"])
        assert job.validation_enabled is True
        assert job.validation_split_ratio == 0.2
        assert job.split_method == "random"

    def test_validation_fields_exposed_in_list_api(self):
        u, p, a = _setup()
        ds = _dataset_with_pairs(a)
        FinetuningJob.objects.create(
            project=p,
            dataset=ds,
            base_model="m",
            validation_enabled=False,
            validation_split_ratio=0.15,
            split_method=FinetuningJob.SplitMethod.ORDERED,
        )

        with patch(CELERY_PATH):
            r = auth_client(u).get(reverse("finetuningjob-list"))

        assert r.status_code == 200
        row = r.data["results"][0]
        assert row["validation_enabled"] is False
        assert row["validation_split_ratio"] == 0.15
        assert row["split_method"] == "ordered"
        assert row["validation_dataset"] is None


def _ft_job(project: Project, dataset: Dataset, **overrides) -> FinetuningJob:
    defaults = {
        "project": project,
        "dataset": dataset,
        "base_model": "meta-llama/Llama-3.2-3B-Instruct",
        "validation_enabled": True,
        "validation_split_ratio": 0.2,
        "split_method": FinetuningJob.SplitMethod.ORDERED,
    }
    defaults.update(overrides)
    defaults.setdefault("cell", defaults["dataset"].active_cell)
    if defaults.get("validation_dataset") is not None:
        defaults.setdefault("validation_cell", defaults["validation_dataset"].active_cell)
    return FinetuningJob.objects.create(**defaults)


class TestFinetuningMaterialisation:
    def _submit(self, job, fake_modal) -> tuple[list[str], list[str] | None, dict]:
        from overbae.tasks.finetuning import run_finetuning

        prepare_training(job, fake_modal)
        assert run_finetuning(job_id=str(job.id))["status"] == "running"
        [(_, _, args, kwargs)] = [e for e in fake_modal.log if e[1] == "upload_dataset"]
        call = dict(zip(("run_id", "data_jsonl", "val_jsonl"), args, strict=False)) | kwargs
        event = FinetuningJobEvent.objects.get(
            job=job, event_type="log", message__startswith="Materialised"
        )
        train = call["data_jsonl"].splitlines()
        validation = call.get("val_jsonl")
        return train, (validation.splitlines() if validation else None), event.data

    def test_a_split_holds_out_the_validation_rows(self, sft, fake_modal):
        _, p, a = _setup()
        job = _ft_job(p, _dataset_with_messages(a, n=10))
        train, validation, meta = self._submit(job, fake_modal)
        assert (len(train), len(validation)) == (8, 2)
        assert meta["validation_mode"] == "split"
        assert (meta["train_examples"], meta["val_examples"]) == (8, 2)

    def test_a_separate_validation_dataset_is_sent_whole(self, sft, fake_modal):
        _, p, a = _setup()
        job = _ft_job(
            p, _dataset_with_messages(a, n=5), validation_dataset=_dataset_with_messages(a, n=3)
        )
        train, validation, meta = self._submit(job, fake_modal)
        assert (len(train), len(validation)) == (5, 3)
        assert meta["validation_mode"] == "separate"

    def test_disabled_validation_sends_every_row_to_training(self, sft, fake_modal):
        _, p, a = _setup()
        job = _ft_job(p, _dataset_with_messages(a, n=5), validation_enabled=False)
        train, validation, meta = self._submit(job, fake_modal)
        assert len(train) == 5
        assert all(f"Question {i}" in "".join(train) for i in range(5))
        assert validation is None
        assert meta["validation_mode"] == "off"
        assert meta["val_examples"] == 0


class TestGPUSelectorLogic:
    pytestmark = pytest.mark.django_db(transaction=False)

    def _cfg(
        self,
        total_params_b: float,
        fp8: bool,
        num_attn_layers: int,
        num_kv_heads: int,
        head_dim: int,
        *,
        static_gpu: str = "L4",
    ) -> dict:
        return {
            "id": "test-model",
            "total_params_b": total_params_b,
            "fp8_supported": fp8,
            "num_attn_layers": num_attn_layers,
            "num_kv_heads": num_kv_heads,
            "head_dim": head_dim,
            "inference": {"gpu_type": static_gpu},
        }

    def test_compact_fp8_short_context_picks_l4(self):
        from overbae.modal.gpu_selector import select_gpu

        gpu, conc = select_gpu(self._cfg(0.5, True, 24, 2, 64), 8192)
        assert gpu == "L4"
        assert conc >= 1

    def test_8b_fp8_at_8k_context_fits_l4(self):
        from overbae.modal.gpu_selector import select_gpu

        gpu, conc = select_gpu(self._cfg(8.0, True, 32, 8, 128), 8192)
        assert gpu == "L4"
        assert conc >= 1

    def test_8b_fp8_at_131k_context_needs_l40s(self):
        """L4 has 24 GB; 8 GB FP8 weights + 8×32×128×2 KV at 131 k tokens ≈ 17 GB — doesn't fit."""
        from overbae.modal.gpu_selector import select_gpu

        gpu, conc = select_gpu(self._cfg(8.0, True, 32, 8, 128), 131072)
        assert gpu == "L40S"
        assert conc >= 1

    def test_70b_fp8_at_131k_picks_h200_not_b200(self):
        """FP8 70B (70 GB) + KV at 131k context fits on H200 (141 GB) — B200 not needed."""
        from overbae.modal.gpu_selector import select_gpu

        gpu, conc = select_gpu(self._cfg(70.0, True, 80, 8, 128, static_gpu="B200"), 131072)
        assert gpu == "H200"

    def test_70b_bf16_at_131k_context_needs_b300(self):
        """BF16 70B weights = 140 GB; KV at 131k ≈ 40 GB → B200 budget only 31 GB, needs B300."""
        from overbae.modal.gpu_selector import select_gpu

        gpu, conc = select_gpu(self._cfg(70.0, False, 80, 8, 128, static_gpu="B300"), 131072)
        assert gpu == "B300"

    def test_32b_fp8_at_40k_fits_a100(self):
        """Qwen3-32B FP8 (32 GB) + KV at 40k tokens fits on A100-80GB with concurrent=3."""
        from overbae.modal.gpu_selector import select_gpu

        gpu, conc = select_gpu(self._cfg(32.0, True, 64, 8, 128, static_gpu="A100-80GB"), 40960)
        assert gpu == "A100-80GB"
        assert conc >= 2

    def test_32b_fp8_at_32k_does_not_squeeze_onto_l40s(self):
        """A 32B at 32k OOMs on an L40S: measured non-KV overhead is 3.25 GiB, and
        a concurrent=1 fit has zero slack by construction."""
        from overbae.modal.gpu_selector import select_gpu

        gpu, conc = select_gpu(self._cfg(32.0, True, 64, 8, 128, static_gpu="A100-80GB"), 32768)
        assert gpu == "A100-80GB"
        assert conc >= 2

    def test_hybrid_model_small_kv_due_to_few_attn_layers(self):
        """Qwen3.5-0.8B has 6 full-attention layers (not 24 total) → tiny KV cache → high concurrency."""
        from overbae.modal.gpu_selector import select_gpu

        gpu, conc = select_gpu(self._cfg(0.8, True, 6, 2, 256), 131072)
        assert gpu == "L4"
        # KV/tok = 2*6*2*256*2 = 12 288 B; at 131072 tokens = 1.5 GB; L4 budget ≈ 19 GB → ≥ 12 concurrent
        assert conc >= 8

    def test_missing_arch_constants_falls_back_to_static_gpu(self):
        from overbae.modal.gpu_selector import select_gpu

        cfg = {
            "id": "unknown-model",
            "total_params_b": 10.0,
            "fp8_supported": True,
            "inference": {"gpu_type": "L40S"},
        }
        gpu, conc = select_gpu(cfg, 8192)
        assert gpu == "L40S"

    def test_missing_arch_constants_and_no_static_gpu_still_fits_weights(self):
        from overbae.modal.gpu_selector import GPU_TIERS, select_gpu

        vram = {t["name"]: t["vram_gb"] for t in GPU_TIERS}
        for params_b, fp8 in ((70.0, True), (70.0, False), (32.0, False), (0.5, True)):
            cfg = {"id": "unknown", "total_params_b": params_b, "fp8_supported": fp8}
            gpu, _ = select_gpu(cfg, 8192)
            weights_gb = params_b * (1 if fp8 else 2)
            assert vram[gpu] * 0.90 > weights_gb, (
                f"{params_b}B fp8={fp8} → {gpu} ({vram[gpu]} GB) cannot hold "
                f"{weights_gb} GB of weights"
            )

    def test_max_concurrent_is_capped(self):
        from overbae.modal.gpu_selector import MAX_CONCURRENT, select_gpu

        _, conc = select_gpu(self._cfg(0.5, True, 16, 2, 64), 512)
        assert conc <= MAX_CONCURRENT

    def test_concurrency_at_least_one(self):
        from overbae.modal.gpu_selector import select_gpu

        _, conc = select_gpu(self._cfg(70.0, True, 80, 8, 128), 131072)
        assert conc >= 1


class TestModelRegistryAnyBackend:
    pytestmark = pytest.mark.django_db(transaction=False)

    def test_together_model_is_found(self):
        from overbae.modal.model_registry import get_model_config_any_backend

        cfg = get_model_config_any_backend("Qwen/Qwen3-8B")
        assert cfg is not None
        assert cfg["id"] == "Qwen/Qwen3-8B"

    def test_baseten_only_model_is_found(self):
        """Qwen2.5-32B is baseten-only; must not return None."""
        from overbae.modal.model_registry import get_model_config_any_backend

        cfg = get_model_config_any_backend("Qwen/Qwen2.5-32B-Instruct")
        assert cfg is not None
        assert cfg["backend"] == "baseten"

    def test_arch_constants_present_on_result(self):
        from overbae.modal.model_registry import get_model_config_any_backend

        cfg = get_model_config_any_backend("Qwen/Qwen2.5-7B-Instruct")
        assert cfg["num_attn_layers"] == 28
        assert cfg["num_kv_heads"] == 4
        assert cfg["head_dim"] == 128

    def test_llama_70b_arch_constants(self):
        from overbae.modal.model_registry import get_model_config_any_backend

        cfg = get_model_config_any_backend("meta-llama/Llama-3.3-70B-Instruct")
        assert cfg["num_attn_layers"] == 80
        assert cfg["num_kv_heads"] == 8
        assert cfg["head_dim"] == 128

    def test_unknown_model_returns_none(self):
        from overbae.modal.model_registry import get_model_config_any_backend

        assert get_model_config_any_backend("totally-unknown/model-xyz") is None

    def test_all_non_disabled_baseten_models_have_arch_constants(self):
        from overbae.modal.model_registry import get_all_models_by_backend

        models = get_all_models_by_backend("baseten", include_disabled=False)
        assert models
        for model_id, cfg in models.items():
            assert cfg.get("num_attn_layers") is not None, f"{model_id} missing num_attn_layers"
            assert cfg.get("num_kv_heads") is not None, f"{model_id} missing num_kv_heads"
            assert cfg.get("head_dim") is not None, f"{model_id} missing head_dim"


class TestBasetenContextLengthPersistence:
    """``submit()`` persists context_length so model_deployment can size the GPU.
    The stubbed push response is Baseten's real push-API shape."""

    pytestmark = pytest.mark.django_db

    def _make_job(self, base_model: str = "Qwen/Qwen3-8B", max_token_length: int = 500):
        _, p, a = _setup()
        ds = _dataset_with_messages(a, n=5)
        version = ds.active_cell
        version.stats = {**version.stats, "max_token_length": max_token_length}
        version.save(update_fields=["stats"])
        return FinetuningJob.objects.create(
            project=p,
            dataset=ds,
            cell=version,
            base_model=base_model,
            hyperparameters={"n_epochs": 2, "training_type": {"type": "Lora", "lora_r": 8}},
        )

    @staticmethod
    def _submit(job, tmp_path):
        train = tmp_path / "train.jsonl"
        train.write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "hi"},
                        {"role": "assistant", "content": "yo"},
                    ]
                }
            )
            + "\n",
            encoding="utf-8",
        )
        truss_train = MagicMock()
        truss_train.push.return_value = {
            "id": "bt-job-1",
            "training_project": {"id": "bt-proj-1"},
        }
        runner = BasetenRunner()
        # truss is a worker-only dep; stub auth rewrite + push at the boundary.
        with (
            patch.dict("sys.modules", {"truss_train": truss_train}),
            patch.object(BasetenRunner, "_ensure_trussrc"),
        ):
            return runner.submit(job=job, training_file_path=str(train), num_examples=4)

    def test_submit_writes_context_length_to_db(self, tmp_path):
        """11,051-token rows plus headroom snap up to the 16,384 bucket — well under
        Qwen3-8B's catalog max of 40,960."""
        job = self._make_job(max_token_length=11051)
        assert "context_length" not in (job.hyperparameters or {})

        result = self._submit(job, tmp_path)
        assert result.remote_id == "bt-proj-1:bt-job-1"

        job.refresh_from_db()
        assert job.hyperparameters["context_length"] == 16384

    def test_submit_preserves_existing_hyperparameters(self, tmp_path):
        job = self._make_job(max_token_length=500)
        self._submit(job, tmp_path)

        job.refresh_from_db()
        assert job.hyperparameters["n_epochs"] == 2
        assert job.hyperparameters["context_length"] == 4096

    def test_context_snap_helper_capped_by_model_max(self):
        from overbae.services.finetuning_policy import TrainingPlanError
        from overbae.services.finetuning_runner import baseten_context_length

        assert baseten_context_length(11051, model_max=32768) == 16384
        with pytest.raises(TrainingPlanError, match="refusing to clamp"):
            baseten_context_length(999_999, model_max=32768)

    def test_training_gpu_scales_with_model_size(self):
        runner = BasetenRunner()
        small = SimpleNamespace(base_model="Qwen/Qwen3-8B")
        big = SimpleNamespace(base_model="Qwen/Qwen2.5-72B-Instruct")
        assert runner._select_training_gpu(small) == ("H100", 1)
        assert runner._select_training_gpu(big) == ("H100", 1)

    def test_gemma4_full_never_gets_multi_gpu(self):
        from overbae.services.finetuning_runner import clamp_gemma4_training_gpu

        assert clamp_gemma4_training_gpu("google/gemma-4-31B-it", "H100", 4) == ("H200", 1)
        assert clamp_gemma4_training_gpu("Qwen/Qwen3-32B", "H100", 4) == ("H100", 4)

    def test_a_modal_run_stages_the_hub_base_weights_before_the_gpu_starts(self, sft, fake_modal):
        from overbae.services.deployment import get_hf_base
        from overbae.tasks.finetuning import run_finetuning

        _, p, a = _setup()
        job = _ft_job(
            p,
            _dataset_with_messages(a, n=5),
            base_model="nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B",
            validation_enabled=False,
        )
        prepare_training(job, fake_modal)
        run_finetuning(job_id=str(job.id))

        names = [name for _, name, _, _ in fake_modal.log]
        gpu = next(n for n in names if n.startswith("sft_"))
        assert names.index("fetch_base_model") < names.index(gpu)
        [(_, _, _, fetched)] = [e for e in fake_modal.log if e[1] == "fetch_base_model"]
        [(_, _, args, kwargs)] = [e for e in fake_modal.log if e[1] == gpu]
        env = kwargs.get("env") or args[1]
        assert fetched["base_model"] == get_hf_base(job.base_model)
        assert env["MODEL_ID"] == "unsloth/NVIDIA-Nemotron-3.5-Lightning-30B-A3B"


class TestDeploymentGPUDerivation:
    pytestmark = pytest.mark.django_db(transaction=False)

    def _select(self, model_id: str, training_ctx: int):
        """Replicate the exact GPU-selection logic from model_deployment.py."""
        from overbae.modal.gpu_selector import select_gpu
        from overbae.modal.model_registry import get_model_config_any_backend

        model_cfg = get_model_config_any_backend(model_id) or {}
        static_max = (model_cfg.get("inference") or {}).get("max_model_len") or 8192
        max_model_len = training_ctx if training_ctx > 0 else static_max
        return select_gpu(model_cfg, max_model_len)

    def test_qwen25_7b_at_32k_picks_l4(self):
        gpu, _ = self._select("Qwen/Qwen2.5-7B-Instruct", 32768)
        assert gpu == "L4"

    def test_llama_8b_at_131k_picks_l40s(self):
        """8B at 131k context: KV cache pushes past L4 budget → L40S."""
        gpu, _ = self._select("meta-llama/Llama-3.1-8B-Instruct", 131072)
        assert gpu == "L40S"

    def test_llama_70b_at_131k_picks_h200(self):
        """70B FP8 at 131k context fits H200; no need for B200."""
        gpu, _ = self._select("meta-llama/Llama-3.3-70B-Instruct", 131072)
        assert gpu == "H200"

    def test_qwen3_32b_at_40k_picks_a100(self):
        gpu, _ = self._select("Qwen/Qwen3-32B", 40960)
        assert gpu == "A100-80GB"

    def test_zero_training_ctx_falls_back_to_static_max_model_len(self):
        from overbae.modal.gpu_selector import select_gpu
        from overbae.modal.model_registry import get_model_config_any_backend

        model_cfg = get_model_config_any_backend("Qwen/Qwen3-8B")
        static_max = model_cfg["inference"]["max_model_len"]  # 16384
        # Simulate: training_ctx = 0 → deployment uses static_max
        max_model_len = 0 if 0 > 0 else static_max
        gpu, _ = select_gpu(model_cfg, max_model_len)
        # At 16k context, 8B FP8 fits on L4
        assert gpu == "L4"

    def test_baseten_only_model_selects_correctly(self):
        """Qwen2.5-32B is baseten-only — get_model_config_any_backend must find it."""
        gpu, conc = self._select("Qwen/Qwen2.5-32B-Instruct", 32768)
        # 32B dense model at 32k — mid-range GPU, not L4 or H200+.
        assert gpu in ("L40S", "A100-80GB")
        assert conc >= 1


def _row(dataset, index):
    return _dataset_row(dataset.active_cell, index)
