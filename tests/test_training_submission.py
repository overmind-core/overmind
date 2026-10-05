from unittest.mock import patch

import pytest

from overbae.models import Dataset, FinetuningJob, Project
from overbae.services import training_submission

pytestmark = pytest.mark.django_db


def test_submission_intent_survives_worker_loss_and_never_resubmits():
    project = Project.objects.create(name="Submission", slug="submission")
    dataset = Dataset.objects.create(project=project)
    job = FinetuningJob.objects.create(project=project, dataset=dataset, base_model="fixture")
    first = training_submission.claim(job)
    assert first["state"] == "submitting"
    with pytest.raises(training_submission.SubmissionUnresolvedError):
        training_submission.claim(job)
    training_submission.unknown(job, "connection lost")
    job.refresh_from_db()
    assert job.status == "submission_unknown"
    with pytest.raises(training_submission.SubmissionUnresolvedError):
        training_submission.claim(job)
    remote = f"ft-{job.id}-receipt:fc-fixture"
    training_submission.acknowledge(job, remote)
    job.refresh_from_db()
    assert job.remote_job_id == remote and job.provider_submission["state"] == "acknowledged"
    with pytest.raises(training_submission.SubmissionUnresolvedError):
        training_submission.claim(job)


def test_reconcile_discovers_call_from_own_provider_metadata_without_resubmission():
    from types import SimpleNamespace

    from overbae.services import training_release

    project = Project.objects.create(name="Reconcile", slug="reconcile")
    dataset = Dataset.objects.create(project=project)
    job = FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model="fixture",
        provider="modal",
        requested_configuration={"runtime": training_release.current()},
    )
    training_submission.claim(job)
    run_id = f"ft-{job.id}-fixture"
    training_submission.dispatching(job, run_id)
    training_submission.unknown(job, TimeoutError())
    job.refresh_from_db()
    with (
        patch("modal.Function.from_name") as function,
        patch("overbae.services.finetuning_runner.get_runner") as runner,
    ):
        function.return_value.remote.return_value = {
            "run_id": run_id,
            "meta": {"call_id": "fc-test"},
        }
        runner.return_value.poll.return_value = SimpleNamespace(
            state="running", raw={"meta": {"call_id": "fc-test"}}
        )
        training_submission.recover(job)
    assert job.status == "running" and job.remote_job_id == f"{run_id}:fc-test"


def test_replaced_staging_owner_cannot_dispatch_gpu_work():
    project = Project.objects.create(name="Fenced staging", slug="fenced-staging")
    dataset = Dataset.objects.create(project=project)
    job = FinetuningJob.objects.create(project=project, dataset=dataset, base_model="fixture")
    training_submission.claim(job)
    FinetuningJob.objects.filter(pk=job.pk).update(provider_submission={})
    replacement = FinetuningJob.objects.get(pk=job.pk)
    training_submission.claim(replacement)
    with pytest.raises(training_submission.SubmissionUnresolvedError):
        training_submission.dispatching(job, f"ft-{job.id}-stale")
    training_submission.unknown(job, RuntimeError("stale staging owner returned"))
    replacement.refresh_from_db()
    assert replacement.provider_submission["state"] == "submitting"
    training_submission.dispatching(replacement, f"ft-{job.id}-current")


@pytest.mark.parametrize(
    "requested,field",
    [
        ({"batch_size": 999}, "batch_size"),
        ({"context_length": 9000}, "context_length"),
        ({"packing": True, "objective": "decision_cross_entropy"}, "packing"),
    ],
)
def test_explicit_recipe_cannot_be_silently_changed(requested, field):
    from overbae.services.finetuning_policy import TrainingPlanError, derive_baseten_training_plan

    with pytest.raises(TrainingPlanError, match=field):
        derive_baseten_training_plan(
            hyperparameters=requested,
            num_train_examples=100,
            dataset_stats={},
            params_b=4,
            model_max_context=8192,
            model_min_batch=1,
        )


def test_explicit_zero_warmup_and_disabled_packing_are_preserved():
    from overbae.services.finetuning_policy import derive_baseten_training_plan

    plan = derive_baseten_training_plan(
        hyperparameters={"warmup_ratio": 0, "packing": False},
        num_train_examples=5000,
        dataset_stats={"avg_input_chars": 4},
        params_b=4,
        model_max_context=8192,
        model_min_batch=1,
    )
    assert plan.warmup_ratio == 0
    assert plan.packing is False
