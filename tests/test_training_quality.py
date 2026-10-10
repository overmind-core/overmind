import asyncio
import copy
import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from modal_shared.training_monitoring import classification_metrics, fingerprint, resolve_policy
from overbae.models import (
    APIToken,
    Dataset,
    FinetuningJob,
    OperationalEvent,
    Project,
    ProjectMembership,
    User,
)
from overbae.services import training_monitoring
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext


@pytest.fixture
def quality_job():
    user = User.objects.create_user(email=f"quality-{uuid.uuid4()}@example.com", password="test")
    project = Project.objects.create(name="Quality observations")
    ProjectMembership.objects.create(user=user, project=project)
    policy = resolve_policy(
        {"generation": {"kind": "classification", "labels": ["a", "b", "c"]}},
        has_development=True,
        provider="modal",
    )
    job = FinetuningJob.objects.create(
        project=project,
        dataset=Dataset.objects.create(project=project),
        status="running",
        base_model="Qwen/Qwen3-0.6B",
        hyperparameters={"monitoring": policy},
    )
    return job, user


def record(job, *, step=2, examples=None):
    if examples is None:
        examples = [
            {"reference": "a", "prediction": "b", "status": "completed"},
            {"reference": "a", "prediction": "a", "status": "completed"},
            {"reference": "a", "prediction": "a", "status": "completed"},
            {"reference": "b", "prediction": "a", "status": "completed"},
            {"reference": "c", "prediction": "a", "status": "completed"},
        ]
    return {
        "key": f"1:development:{step}",
        "attempt": 1,
        "stream": "development",
        "step": step,
        "state": "completed",
        "started_at": 100,
        "observed_at": 110,
        "policy_fingerprint": fingerprint(job.hyperparameters["monitoring"]),
        "sample_fingerprint": "b" * 64,
        "facts": {"findings": []},
        "metrics": {
            "eval_loss": 0.1,
            "generation": classification_metrics(examples, ["a", "b", "c"]),
        },
        "coverage": {"expected": 5, "scored": 5},
    }


@pytest.mark.django_db(transaction=True)
def test_collector_retains_quality_assessment_and_later_loss_does_not_hide_it(quality_job):
    job, user = quality_job
    source = record(job)
    original = copy.deepcopy(source)
    training_monitoring.ingest(job, {"checks": [source]})
    check = job.validation_runs.get()
    facts = check.facts["assessment"]
    assert facts["source"] == "recorded_generation_metrics"
    assert facts["source_receipt_fingerprint"] == check.receipt_fingerprint
    assert facts["state"] == "measured"
    assert {finding["code"] for finding in facts["findings"]} == {
        "represented_labels_not_predicted",
        "below_probe_majority_baseline",
    }
    counts = facts["comparison"]
    assert counts == {
        "expected": 5,
        "scored": 5,
        "correct": 2,
        "majority_correct": 3,
        "majority_label_indices": [0],
        "unpredicted_label_indices": [2],
    }
    assert check.metrics == source["metrics"]
    assert source == original
    assert check.facts["findings"] == []
    events = OperationalEvent.objects.count()
    training_monitoring.ingest(job, {"checks": [source]})
    check.refresh_from_db()
    assert check.facts["assessment"] == facts
    assert OperationalEvent.objects.count() == events
    later = record(job, step=4)
    later["metrics"].pop("generation")
    training_monitoring.ingest(job, {"checks": [later]})

    context = MCPContext(
        user=user,
        project=job.project,
        token=APIToken(scope={"scope": "project", "permission": ["read"]}),
    )
    client = APIClient()
    client.force_authenticate(user)
    with patch("modal.Function.from_name", side_effect=AssertionError("read invoked provider")):
        result = asyncio.run(
            CATALOG.call("get_job", {"kind": "finetune_job", "id": str(job.id)}, context)
        )
        snapshot = asyncio.run(
            CATALOG.call("inspect_training_progress", {"job": str(job.id)}, context)
        )
        rest = client.get(reverse("finetuningjob-monitoring", kwargs={"id": job.id}))
    assert not result.isError, result.structuredContent
    summary = result.structuredContent["details"]["monitoring"]
    assert summary["latest_check"]["step"] == 4
    assert summary["latest_generation_check"]["id"] == str(check.id)
    assert summary["latest_generation_check"]["assessment"] == facts
    assert summary["latest_generation_check"]["step"] == 2
    assert snapshot.structuredContent["progress"]["checks"] == rest.json()["checks"]
    assert rest.json()["checks"][0]["facts"]["assessment"] == facts
    assert OperationalEvent.objects.count() == events + 1
    job.refresh_from_db()
    assert job.status == "running"


@pytest.mark.django_db
def test_late_collection_adds_assessment_without_rewriting_existing_worker_receipt(quality_job):
    job, _ = quality_job
    source = record(job)
    training_monitoring.ingest(job, {"checks": [source]})
    check = job.validation_runs.get()
    check.facts.pop("assessment", None)
    check.save(update_fields=["facts"])
    receipt_digest, metrics, observed = check.receipt_fingerprint, check.metrics, check.observed_at
    training_monitoring.ingest(job, {"checks": [source]})
    check.refresh_from_db()
    assert check.facts["assessment"]["source_receipt_fingerprint"] == receipt_digest
    assert check.metrics == metrics and check.observed_at == observed
    with pytest.raises(ValueError, match="changed"):
        training_monitoring.ingest(job, {"checks": [{**source, "metrics": {"eval_loss": 1}}]})


@pytest.mark.django_db
def test_comparison_uses_only_scorable_support_not_failed_reference_counts(quality_job):
    job, _ = quality_job
    examples = [{"reference": "a", "prediction": "a", "status": "completed"}] + [
        {"reference": "b", "status": "failed"}
    ] * 10
    training_monitoring.ingest(job, {"checks": [record(job, examples=examples)]})
    assessment = job.validation_runs.get().facts["assessment"]
    assert assessment["comparison"]["scored"] == 1
    assert assessment["comparison"]["majority_correct"] == 1
    assert assessment["comparison"]["unpredicted_label_indices"] == []
    assert assessment["findings"] == []


@pytest.mark.django_db
def test_new_attempt_generation_remains_visible_when_steps_restart(quality_job):
    job, _ = quality_job
    training_monitoring.ingest(job, {"checks": [record(job, step=100)]})
    resumed = record(job, step=0)
    resumed.update(key="2:development:0", attempt=2)
    training_monitoring.ingest(job, {"checks": [resumed]})
    summary = training_monitoring.summary(job)
    assert summary["latest_generation_check"]["attempt"] == 2
    assert summary["latest_check"]["step"] == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mutation",
    [
        "missing_counts",
        "wrong_labels",
        "negative_count",
        "mismatched_counts",
        "nonfinite_accuracy",
        "overflow_accuracy",
        "wrong_policy",
        "no_scorable_rows",
    ],
)
def test_incomplete_or_inconsistent_classification_cannot_produce_quality_claims(
    quality_job, mutation
):
    job, _ = quality_job
    source = record(job)
    generation = source["metrics"]["generation"]
    if mutation == "missing_counts":
        generation.pop("per_class")
    elif mutation == "wrong_labels":
        generation["labels"] = ["c", "b", "a"]
    elif mutation == "negative_count":
        generation["prediction_distribution"]["a"] = -1
    elif mutation == "mismatched_counts":
        generation["scored"] = 2
    elif mutation == "nonfinite_accuracy":
        generation["accuracy"] = None
    elif mutation == "overflow_accuracy":
        generation["accuracy"] = 10**400
    elif mutation == "wrong_policy":
        source["policy_fingerprint"] = "f" * 64
    else:
        generation.update(classification_metrics([], ["a", "b", "c"]))
    training_monitoring.ingest(job, {"checks": [source]})
    assessment = job.validation_runs.get().facts["assessment"]
    assert assessment["state"] == "inconclusive"
    assert assessment["comparison"] is None
    assert assessment["findings"] == []
