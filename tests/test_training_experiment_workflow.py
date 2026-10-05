from unittest.mock import Mock, patch

import pytest
from conftest import frozen_dataset

from modal_shared.decision_checkpoint_policy import select_checkpoint, validate_policy
from modal_shared.runtime_profile import representative_order
from overbae.models import Dataset, FinetuningJob, Project, ProjectMembership, User
from overbae.services import native_evaluation, training_experiments
from overbae.services.datasets import land, rows

pytestmark = pytest.mark.django_db


def data(project, name):
    dataset = Dataset.objects.create(project=project, name=name, intent="train")
    land.land_rows(
        dataset,
        [
            {
                "decision": {
                    "state": f"{name} {i}",
                    "question": "Select",
                    "kind": "choice",
                    "options": ["a", "b"],
                    "target_probabilities": [1, 0],
                }
            }
            for i in range(30)
        ],
    )
    dataset.refresh_from_db()
    return dataset


def test_experiment_freezes_variants_and_launches_each_saved_candidate_once(
    settings, monkeypatch, django_capture_on_commit_callbacks
):

    settings.FINETUNING_BACKEND = "modal"
    monkeypatch.setenv("MODAL_ENVIRONMENT", "test")
    project = Project.objects.create(name="Controlled comparison", slug="controlled")
    user = User.objects.create_user(email="research@example.com")
    ProjectMembership.objects.create(project=project, user=user)
    train, development = data(project, "train"), data(project, "development")
    variants = [
        {
            "name": f"Seed {seed}",
            "base_model": "Qwen/Qwen3.5-4B",
            "cell": str(train.active_cell.pk),
            "development_cell": str(development.active_cell.pk),
            "hyperparameters": {
                "seed": seed,
                "pre_training_baseline": seed == 12,
                "n_epochs": 1,
                "checkpoint_policy": {"fractions": [0.5, 1], "selection": "development_loss"},
            },
        }
        for seed in [11, 12]
    ]
    group = training_experiments.create(
        project,
        user=user,
        name="Seed replication",
        purpose="Measure training-seed variation",
        request_key="seed-study",
        variants=variants,
    )
    assert (
        training_experiments.create(
            project,
            user=user,
            name=group.name,
            purpose=group.purpose,
            request_key="seed-study",
            variants=variants,
        ).pk
        == group.pk
    )
    assert train.cells.get(pk=train.active_cell.pk).used_at
    with (
        patch(
            "overbae.tasks.finetuning.run_finetuning.apply_async", return_value=Mock(id="queued")
        ) as dispatch,
        django_capture_on_commit_callbacks(execute=True),
    ):
        training_experiments.prepare(group)
        training_experiments.launch(group, user=user)
        training_experiments.prepare(group)
        training_experiments.launch(group, user=user)
    assert dispatch.call_count == 2
    jobs = list(FinetuningJob.objects.filter(group_id=group.id).order_by("name"))
    assert [job.hyperparameters["pre_training_baseline"] for job in jobs] == [False, True]
    assert all(job.validation_enabled for job in jobs)
    assert FinetuningJob.objects.filter(group_id=group.pk).count() == 2
    report = training_experiments.describe(group)
    assert "hyperparameters.seed" in report["varying_fields"]
    assert report["seed_variation"] == "separate_from_evaluation_group_uncertainty"
    assert len(report["jobs"]) == 2
    assert all(
        job.hyperparameters["n_epochs"] == 1
        for job in FinetuningJob.objects.filter(group_id=group.pk)
    )


def test_retained_checkpoint_policy_requires_development_and_refuses_final_selection():

    for value in [
        {"fractions": [0.5, 1], "selection": "final_accuracy"},
        {"fractions": [0, 1], "selection": "last"},
        {"fractions": [1], "selection": "development_loss"},
    ]:
        with pytest.raises(ValueError):
            validate_policy(value, has_development=False)


def test_checkpoint_selection_excludes_unverified_artifacts_and_uses_development_only():

    policy = {"fractions": [0.5, 1], "selection": "development_loss"}
    records = [
        {
            "step": 10,
            "development_loss": 0.2,
            "final_accuracy": 0,
            "reload_verification": {"decisions": 3, "max_absolute_error": 0, "tolerance": 1e-4},
        },
        {
            "step": 20,
            "development_loss": 0.3,
            "final_accuracy": 1,
            "reload_verification": {"decisions": 3, "max_absolute_error": 0, "tolerance": 1e-4},
        },
    ]
    assert select_checkpoint(policy, records)["step"] == 10
    records[0]["reload_verification"]["max_absolute_error"] = 1
    with pytest.raises(ValueError, match="reload"):
        select_checkpoint(policy, records)


def test_experiment_records_training_overlap_with_calibration_without_quality_blocking():

    project = Project.objects.create(name="Sealed suites", slug="sealed-suites")
    user = User.objects.create_user(email="sealed@example.com")
    ProjectMembership.objects.create(project=project, user=user)
    train = data(project, "train")
    training_rows = list(rows.iter_rows(train.active_cell))

    def evaluation_row(state):
        return {
            "input": {
                "decision": {
                    "state": state,
                    "question": "Select",
                    "kind": "choice",
                    "options": ["a", "b"],
                }
            },
            "expected_output": {"probabilities": [1, 0]},
        }

    calibration = frozen_dataset(
        project, [evaluation_row(training_rows[0].extra["decision"]["state"])], contract="eval"
    )
    final = frozen_dataset(project, [evaluation_row("never in training")], contract="eval")
    with patch.object(
        native_evaluation, "runtime", return_value={"app": "offline", "environment": "test"}
    ):
        evaluation = native_evaluation.create_plan(
            project,
            name="Sealed",
            request_key="sealed",
            participants=[
                {
                    "key": "external",
                    "name": "External",
                    "kind": "external",
                    "model": "typesafe/jev-1.13",
                }
            ],
            baseline="external",
            final_cell=final.active_cell,
            calibration_cell=calibration.active_cell,
        )
    experiment = training_experiments.create(
        project,
        user=user,
        name="Leak",
        purpose="Inspect leakage",
        request_key="leak",
        variants=[
            {
                "name": "Candidate",
                "base_model": "Qwen/Qwen3.5-4B",
                "cell": str(train.active_cell.pk),
                "hyperparameters": {"n_epochs": 1, "seed": 1},
            }
        ],
        evaluation=evaluation,
    )

    experiment = training_experiments.prepare(experiment)
    assert (
        experiment.protocol["boundaries"][str(train.active_cell.pk)]["calibration"]["overlap_count"]
        == 1
    )


def test_saved_experiment_budget_blocks_unknown_quote_without_launching(settings, monkeypatch):
    settings.FINETUNING_BACKEND = "modal"
    monkeypatch.setenv("MODAL_ENVIRONMENT", "test")
    project = Project.objects.create(name="Budget", slug="budget")
    user = User.objects.create_user(email="budget@example.com")
    ProjectMembership.objects.create(project=project, user=user)
    train = data(project, "train")
    group = training_experiments.create(
        project,
        user=user,
        name="Bounded",
        purpose="Compare recipes",
        request_key="bounded",
        variants=[
            {
                "name": "Candidate",
                "base_model": "Qwen/Qwen3.5-4B",
                "cell": str(train.active_cell.pk),
                "hyperparameters": {"seed": 12, "n_epochs": 1},
            }
        ],
        constraints={"max_training_run_usd": 70},
    )
    with (
        patch("overbae.tasks.finetuning.run_finetuning.apply_async") as dispatch,
        pytest.raises(ValueError, match="forecast"),
    ):
        training_experiments.prepare(group)
        training_experiments.launch(group, user=user)
    dispatch.assert_not_called()
    assert not FinetuningJob.objects.filter(group_id=group.pk).exists()


def test_profile_draft_pins_runtime_bounds_without_submitting(settings, monkeypatch):
    settings.FINETUNING_BACKEND = "modal"
    monkeypatch.setenv("MODAL_ENVIRONMENT", "test")
    project = Project.objects.create(name="Profile", slug="profile")
    user = User.objects.create_user(email="profile@example.com")
    ProjectMembership.objects.create(project=project, user=user)
    train = data(project, "train")
    variant = {
        "name": "Profile",
        "base_model": "Qwen/Qwen3.5-4B",
        "cell": str(train.active_cell.pk),
        "hyperparameters": {"seed": 37, "n_epochs": 1},
    }
    experiment = training_experiments.create_profile(
        project,
        user=user,
        name="Profile",
        request_key="profile",
        variant=variant,
        max_steps=16,
        max_seconds=600,
    )
    assert experiment.state == "draft"
    assert experiment.variants[0]["hyperparameters"]["runtime_profile"] == {
        "max_steps": 16,
        "max_seconds": 600,
    }
    assert not FinetuningJob.objects.filter(group_id=experiment.pk).exists()


def test_profile_sampling_spans_length_distribution_without_duplicate_observations():

    lengths = [8] * 9900 + [4096] * 100
    order, report = representative_order(lengths, samples=64, batch_size=8, seed=9)
    assert len(order) == len(lengths) and len(set(order)) == len(lengths)
    assert min(lengths[i] for i in order[:64]) == 8
    assert max(lengths[i] for i in order[:64]) == 4096
    assert report["selected_rows"] == 64
    assert report["quality_evidence"] is False


def test_experiment_reports_missing_native_artifact_instead_of_completion(settings, monkeypatch):
    settings.FINETUNING_BACKEND = "modal"
    monkeypatch.setenv("MODAL_ENVIRONMENT", "test")
    project = Project.objects.create(name="Verified outputs")
    user = User.objects.create_user(email="verified-outputs@example.com")
    ProjectMembership.objects.create(project=project, user=user)
    source = data(project, "train")
    experiment = training_experiments.create(
        project,
        user=user,
        name="Outputs",
        purpose="A verified model",
        request_key="outputs",
        variants=[
            {
                "name": "candidate",
                "base_model": "Qwen/Qwen3.5-4B",
                "cell": str(source.active_cell.pk),
                "hyperparameters": {"n_epochs": 1},
            }
        ],
    )
    training_experiments.prepare(experiment)
    with patch.object(training_experiments, "dispatch"):
        training_experiments.launch(experiment, user=user)
    FinetuningJob.objects.filter(group_id=experiment.pk).update(status="succeeded", result={})
    experiment.refresh_from_db()
    training_experiments.reconcile(experiment)
    experiment.refresh_from_db()
    assert experiment.state == "incomplete"
    FinetuningJob.objects.filter(group_id=experiment.pk).update(
        result={
            "artifact_identity": "sealed",
            "reload_verification": {"decisions": 3, "max_absolute_error": 0, "tolerance": 1e-4},
        }
    )
    training_experiments.reconcile(experiment)
    experiment.refresh_from_db()
    assert experiment.state == "completed"
