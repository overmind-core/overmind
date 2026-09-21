import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from django.utils import timezone
from modal.exception import NotFoundError
from rest_framework.test import APIClient

from modal_shared.training_data import materialize_tokens, row_key
from overbae.models import FinetuningJob, Project, ProjectMembership, User
from overbae.services import training_preparation as preparation
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.sft_assets.preprocess import preprocess_rows

pytestmark = pytest.mark.django_db


@pytest.fixture
def cell(settings):
    settings.FINETUNING_BACKEND = "modal"
    project = Project.objects.create(name="Prep", slug="prep")
    return frozen_dataset(project, TRAIN_ROWS).active_cell


def test_exact_preprocessing_reports_tokens_targets_and_incompatible_rows():
    tokenizer = SimpleNamespace(decode=lambda tokens: " ".join(map(str, tokens)))
    samples = [{"messages": [], "source_row": i} for i in range(4)]

    def tokenize(_tok, _model, messages, tools):
        return next(results)

    results = iter(
        [
            {"input_ids": [1, 2, 3, 4], "labels": [-100, -100, 3, 4]},
            {"input_ids": [1, 2, 3], "labels": [-100, -100, -100]},
            {"input_ids": [1, 2, 3, 4, 5], "labels": [-100, 2, 3, 4, 5]},
            {"input_ids": [1], "labels": [1]},
        ]
    )
    artifact, report = preprocess_rows(samples, tokenizer, "test", 4, tokenize)
    assert not report["ready"] and report["incompatible_rows"] == 3
    assert len(artifact) == 1 and artifact[0]["labels"] == [-100, -100, 3, 4]
    assert report["previews"][0]["supervised_content"] == "3 4"
    assert report["previews"][0]["supervised_tokens"] == 2
    assert {issue["row"] for issue in report["issues"]} == {1, 2, 3}


def test_training_materializes_exact_artifact_and_refuses_unvalidated_rows():
    row = TRAIN_ROWS[0]
    tokenized = {"input_ids": [1, 2], "labels": [-100, 2]}
    tokens = {row_key(row): tokenized}
    assert list(materialize_tokens(json.dumps(row), tokens)) == [json.dumps(tokenized) + "\n"]
    with pytest.raises(ValueError, match="validated"):
        list(materialize_tokens(json.dumps(TRAIN_ROWS[1]), tokens))


def test_preparation_caches_exact_version_and_configuration(cell):
    first = preparation.request_preparation(cell, "Qwen/Qwen3-8B", 4096)
    same = preparation.request_preparation(cell, "Qwen/Qwen3-8B", 4096)
    different = preparation.request_preparation(cell, "Qwen/Qwen3-8B", 8192)
    assert first.id == same.id and first.id != different.id
    cell.fingerprint = "different"
    cell.save(update_fields=["fingerprint"])
    with pytest.raises(DatasetError, match="changed") as error:
        preparation.request_preparation(cell, "Qwen/Qwen3-8B", 4096)
    assert error.value.code == "workshop_validation"


def test_preparation_verifies_each_target_once_then_rechecks_in_worker(cell, monkeypatch):
    validation = frozen_dataset(cell.dataset.project, TRAIN_ROWS).active_cell
    with patch.object(
        preparation.row_store, "verify", wraps=preparation.row_store.verify
    ) as verify:
        prep = preparation.request_preparation(
            cell, "Qwen/Qwen3-8B", 4096, validation_cell=validation
        )
    assert [call.args[0].id for call in verify.call_args_list] == [cell.id, validation.id]
    validation.fingerprint = "tampered-after-request"
    validation.save(update_fields=["fingerprint"])
    lookup = Mock()
    monkeypatch.setattr(preparation.modal.Function, "from_name", lookup)
    preparation.advance(prep.id)
    prep.refresh_from_db()
    assert prep.state == "failed" and "changed" in prep.error
    lookup.assert_not_called()


@pytest.mark.parametrize("problem", ["intent", "project"])
def test_preparation_keeps_validation_dataset_boundaries(cell, problem):
    project = (
        Project.objects.create(name="Other", slug="other")
        if problem == "project"
        else cell.dataset.project
    )
    validation = frozen_dataset(project, contract="eval" if problem == "intent" else "train")
    with pytest.raises(ValueError, match="needs train" if problem == "intent" else "same project"):
        preparation.request_preparation(
            cell, "Qwen/Qwen3-8B", 4096, validation_cell=validation.active_cell
        )


def test_export_format_changes_invalidate_cached_preprocessing(cell, monkeypatch):
    first = preparation.request_preparation(cell, "Qwen/Qwen3-8B", 4096)
    monkeypatch.setattr(preparation, "data_format_fingerprint", lambda: "new-format")
    changed = preparation.request_preparation(cell, "Qwen/Qwen3-8B", 4096)
    assert changed.id != first.id
    assert changed.config["data_format"] == "new-format"


def test_worker_reconnects_to_existing_call_without_spawning_again(cell, monkeypatch):
    prep = preparation.request_preparation(cell, "Qwen/Qwen3-8B", 4096)
    spawn = Mock(return_value=SimpleNamespace(object_id="fc-prep"))
    monkeypatch.setattr(
        preparation.modal.Function, "from_name", Mock(return_value=SimpleNamespace(spawn=spawn))
    )
    poll = Mock(side_effect=[TimeoutError(), {"ready": True, "tokens": 20}])
    monkeypatch.setattr(
        preparation.modal.FunctionCall, "from_id", Mock(return_value=SimpleNamespace(get=poll))
    )
    with patch.object(
        preparation.TrainingPreparation.objects,
        "select_for_update",
        wraps=preparation.TrainingPreparation.objects.select_for_update,
    ) as lock:
        preparation.advance(prep.id)
    lock.assert_called_once_with(of=("self",))
    preparation.advance(prep.id)
    preparation.advance(prep.id)
    prep.refresh_from_db()
    assert spawn.call_count == 1 and prep.state == "ready" and prep.remote_id == "fc-prep"
    assert prep.report["tokens"] == 20


def test_uncertain_submission_and_expired_operations_fail_closed(cell):
    prep = preparation.request_preparation(cell, "Qwen/Qwen3-8B", 4096)
    prep.__class__.objects.filter(pk=prep.pk).update(
        state="starting", touched_at=timezone.now() - timedelta(minutes=3)
    )
    preparation.advance(prep.id)
    prep.refresh_from_db()
    assert prep.state == "failed" and "acknowledgement" in prep.error
    with pytest.raises(ValueError, match="safely"):
        preparation.retry_preparation(prep)
    other = preparation.request_preparation(cell, "Qwen/Qwen3-8B", 8192)
    other.deadline = timezone.now() - timedelta(seconds=1)
    other.save(update_fields=["deadline"])
    preparation.advance(other.id)
    other.refresh_from_db()
    assert other.state == "failed" and "deadline" in other.error
    assert preparation.retry_preparation(other).state == "queued"


def test_retry_cancels_confirmed_remote_attempt_and_preserves_uncertain_failures(cell, monkeypatch):
    prep = preparation.request_preparation(cell, "Qwen/Qwen3-8B", 4096)
    prep.state, prep.remote_id, prep.report = "failed", "fc-old", {"retryable": True}
    prep.save()
    cancel = Mock()
    monkeypatch.setattr(
        preparation.modal.FunctionCall, "from_id", Mock(return_value=SimpleNamespace(cancel=cancel))
    )
    preparation.retry_preparation(prep)
    cancel.assert_called_once()
    assert prep.state == "queued" and not prep.remote_id and not prep.report


def test_preparation_api_is_project_scoped_and_does_not_start_training(cell, monkeypatch):
    user = User.objects.create_user(
        email="prep@example.test", password="test", clerk_user_id="prep"
    )
    ProjectMembership.objects.create(project=cell.dataset.project, user=user)
    client = APIClient()
    client.force_authenticate(user)
    queue = Mock()
    monkeypatch.setattr("overbae.api.training_preparation.inspect_preparation.delay", queue)
    body = {"dataset": str(cell.dataset_id), "model": "Qwen/Qwen3-8B", "context_length": 4096}
    response = client.post("/api/training-preparations/", body, format="json")
    assert response.status_code == 202, response.data
    queue.assert_called_once()
    assert client.get(f"/api/training-preparations/{response.data['id']}/").status_code == 200
    ProjectMembership.objects.filter(user=user).delete()
    assert client.get(f"/api/training-preparations/{response.data['id']}/").status_code == 404
    assert client.post("/api/training-preparations/", body, format="json").status_code == 404


@pytest.fixture
def retry_job(cell, monkeypatch, settings):
    settings.STRIPE_SECRET_KEY = ""
    user = User.objects.create_user(
        email="retry@example.test", password="test", clerk_user_id="retry"
    )
    ProjectMembership.objects.create(project=cell.dataset.project, user=user)
    job = FinetuningJob.objects.create(
        project=cell.dataset.project,
        dataset=cell.dataset,
        cell=cell,
        base_model="Qwen/Qwen3-8B",
        hyperparameters={"context_length": 4096},
        provider="modal",
        status="failed",
        error_message="Original preprocessing error",
        triggered_by=user,
    )
    queue = Mock(return_value=SimpleNamespace(id="training-task"))
    monkeypatch.setattr("overbae.tasks.finetuning.run_finetuning.apply_async", queue)
    reset_evals = Mock()
    monkeypatch.setattr(
        "overbae.services.finetuning_eval.reset_before_evals_for_retry", reset_evals
    )
    client = APIClient()
    client.force_authenticate(user)
    return job, client, queue, reset_evals


def test_training_retry_recovers_cached_missing_function_failure(retry_job, monkeypatch):
    job, client, queue, _ = retry_job
    prep = preparation.for_job(job)
    old_deadline = prep.deadline
    spawn = Mock(side_effect=NotFoundError("prepare_sft_u2026_8_18 not found"))
    monkeypatch.setattr(
        preparation.modal.Function, "from_name", Mock(return_value=SimpleNamespace(spawn=spawn))
    )
    preparation.advance(prep.id)
    prep.refresh_from_db()
    assert prep.state == "failed" and prep.report["retryable"]
    assert not prep.remote_id
    assert preparation.for_job(job).id == prep.id

    response = client.post(f"/api/finetuning-jobs/{job.id}/retry/")
    assert response.status_code == 200, response.data
    prep.refresh_from_db()
    job.refresh_from_db()
    assert prep.state == "queued" and not prep.error and not prep.report
    assert prep.deadline > old_deadline
    assert job.status == "queued" and not job.error_message
    queue.assert_called_once_with(kwargs={"job_id": str(job.id)})

    spawn.side_effect = None
    spawn.return_value = SimpleNamespace(object_id="fc-prepared")
    preparation.advance(prep.id)
    prep.refresh_from_db()
    assert prep.state == "running" and prep.remote_id == "fc-prepared"
    assert spawn.call_count == 2


@pytest.mark.parametrize("state", ["ready", "queued", "starting", "running"])
def test_training_retry_preserves_usable_or_inflight_preparation(retry_job, state):
    job, client, queue, _ = retry_job
    prep = preparation.for_job(job)
    prep.state, prep.remote_id, prep.report = state, "fc-existing", {"tokens": 100}
    prep.save()
    deadline = prep.deadline
    response = client.post(f"/api/finetuning-jobs/{job.id}/retry/")
    assert response.status_code == 200, response.data
    prep.refresh_from_db()
    assert prep.state == state and prep.remote_id == "fc-existing"
    assert prep.report == {"tokens": 100} and prep.deadline == deadline
    queue.assert_called_once()


@pytest.mark.parametrize("state", ["failed", "incompatible"])
def test_training_retry_does_not_requeue_unsafe_or_incompatible_preparation(retry_job, state):
    job, client, queue, reset_evals = retry_job
    prep = preparation.for_job(job)
    prep.state, prep.report = state, {"retryable": False}
    prep.save()
    response = client.post(f"/api/finetuning-jobs/{job.id}/retry/")
    assert response.status_code == 400, response.data
    prep.refresh_from_db()
    job.refresh_from_db()
    assert prep.state == state and job.status == "failed"
    assert job.error_message == "Original preprocessing error"
    queue.assert_not_called()
    reset_evals.assert_not_called()


@pytest.mark.parametrize("remote", ["fc-training", ""])
def test_preparation_retry_skips_submitted_training_or_other_backends(retry_job, settings, remote):
    job, _, _, _ = retry_job
    job.remote_job_id = remote
    if not remote:
        settings.FINETUNING_BACKEND = "together"
    with patch.object(preparation, "for_job") as request:
        preparation.retry_for_job(job)
    request.assert_not_called()
