import asyncio
import json
import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from modal_shared.training_monitoring import fingerprint, freeze_plan, resolve_policy, row_identity
from overbae.models import APIToken, Dataset, FinetuningJob, Project, ProjectMembership, User
from overbae.services import training_monitoring
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext


@pytest.mark.django_db(transaction=True)
def test_many_decision_questions_remain_inspectable_through_paged_receipts():
    user = User.objects.create_user(email=f"families-{uuid.uuid4()}@example.com", password="test")
    project = Project.objects.create(name="Question families")
    ProjectMembership.objects.create(user=user, project=project)
    policy = resolve_policy({}, has_development=True, provider="modal")
    job = FinetuningJob.objects.create(
        project=project,
        dataset=Dataset.objects.create(project=project),
        hyperparameters={"objective": "decision_cross_entropy", "monitoring": policy},
    )
    families = {}
    for i in range(800):
        request = {
            "question": f"Is claim {i} supported by the passage?",
            "kind": "noul",
            "options": ["No", "Yes"],
        }
        families[fingerprint(request)] = {
            **request,
            "scored": 1,
            "expected": 1,
            "support": [1, 0],
            "predicted": [0, 1],
            "confusion_matrix": [[0, 1], [0, 0]],
        }
    metrics = {"eval_loss": 0.7, "hard_label_decisions": 800, "categorical_families": families}
    training_monitoring.ingest(
        job,
        {
            "checks": [
                {
                    "key": "1:development:0",
                    "attempt": 1,
                    "stream": "development",
                    "step": 0,
                    "state": "completed",
                    "policy_fingerprint": fingerprint(policy),
                    "sample_fingerprint": "b" * 64,
                    "metrics": metrics,
                    "coverage": {"scored": 800, "expected": 800},
                }
            ]
        },
    )
    check = job.validation_runs.get()
    checkpoint = job.retained_checkpoints.create(key="1:0", attempt=1, step=0, metrics=metrics)
    context = MCPContext(
        user=user,
        project=project,
        token=APIToken(scope={"scope": "project", "permission": ["read"]}),
    )
    client = APIClient()
    client.force_authenticate(user)
    with patch("modal.Function.from_name", side_effect=AssertionError("read invoked provider")):
        result = asyncio.run(
            CATALOG.call("inspect_training_progress", {"job": str(job.pk), "limit": 1}, context)
        )
        assert not result.isError, result.structuredContent
        overview = result.structuredContent["progress"]
        assert len(json.dumps(overview).encode()) < 128 * 1024
        assert overview["checks"][0]["metrics"]["eval_loss"] == 0.7
        pointers = {item["field"]: item for item in overview["checks"][0]["collections"]}
        field = f"/checks/{check.pk}/metrics/categorical_families"
        assert pointers[field]["count"] == 800
        collected, offset = {}, 0
        while offset is not None:
            args = {"job": str(job.pk), "field": field, "offset": offset, "limit": 20}
            page = asyncio.run(CATALOG.call("inspect_training_progress", args, context))
            assert not page.isError, page.structuredContent
            detail = page.structuredContent["progress"]
            rest = client.get(
                reverse("finetuningjob-monitoring-evidence", kwargs={"id": job.pk}),
                {k: v for k, v in args.items() if k != "job"},
            )
            assert rest.status_code == 200 and rest.json() == detail
            collected.update({item["key"]: item["value"] for item in detail["items"]})
            offset = detail["next_offset"]
        assert collected == families
        findings = asyncio.run(
            CATALOG.call(
                "inspect_training_progress",
                {
                    "job": str(job.pk),
                    "field": f"/checks/{check.pk}/facts/assessment/findings",
                    "limit": 1,
                },
                context,
            )
        )
        assert not findings.isError and findings.structuredContent["progress"]["count"] == 1600
        matrix = asyncio.run(
            CATALOG.call(
                "inspect_training_progress",
                {
                    "job": str(job.pk),
                    "field": f"/checkpoints/{checkpoint.pk}/metrics/categorical_families/{next(iter(families))}/confusion_matrix",
                    "limit": 1,
                },
                context,
            )
        )
        assert not matrix.isError and matrix.structuredContent["progress"]["items"] == [[0, 1]]
        foreign = FinetuningJob.objects.create(project=project, dataset=job.dataset)
        denied = asyncio.run(
            CATALOG.call(
                "inspect_training_progress", {"job": str(foreign.pk), "field": field}, context
            )
        )
        assert denied.isError
    check.refresh_from_db()
    assert check.metrics == metrics


@pytest.mark.django_db(transaction=True)
def test_agent_and_console_read_the_same_durable_check_then_cancel_without_losing_evidence():
    user = User.objects.create_user(email=f"monitor-{uuid.uuid4()}@example.com", password="test")
    project = Project.objects.create(name="Monitoring journey")
    ProjectMembership.objects.create(user=user, project=project)
    dataset = Dataset.objects.create(project=project)
    job = FinetuningJob.objects.create(
        project=project, dataset=dataset, status="running", base_model="Qwen/Qwen3-0.6B"
    )
    context = MCPContext(
        user=user,
        project=project,
        token=APIToken(scope={"scope": "project", "permission": ["read", "write"]}),
    )
    training_monitoring.ingest(
        job,
        {
            "checks": [
                {
                    "key": "1:development:14",
                    "attempt": 1,
                    "stream": "development",
                    "step": 14,
                    "state": "completed",
                    "policy_fingerprint": "a" * 64,
                    "sample_fingerprint": "b" * 64,
                    "started_at": 100,
                    "observed_at": 110,
                    "metrics": {
                        "eval_loss": 0.2,
                        "generation": {
                            "pass_rate": 0.75,
                            "scored": 4,
                            "expected": 4,
                            "coverage": 1,
                        },
                    },
                }
            ]
        },
    )
    client = APIClient()
    client.force_authenticate(user)
    with patch("modal.Function.from_name", side_effect=AssertionError("read invoked provider")):
        result = asyncio.run(
            CATALOG.call("inspect_training_progress", {"job": str(job.id)}, context)
        )
        assert not result.isError, result.structuredContent
        response = client.get(reverse("finetuningjob-monitoring", kwargs={"id": job.id}))
    assert response.status_code == 200, response.data
    assert result.structuredContent["progress"]["checks"] == response.json()["checks"]
    assert result.structuredContent["progress"]["checks"][0]["metrics"]["eval_loss"] == 0.2
    compact = asyncio.run(
        CATALOG.call("get_job", {"kind": "finetune_job", "id": str(job.id)}, context)
    )
    assert not compact.isError, compact.structuredContent
    quality = compact.structuredContent["details"]["monitoring"]["latest_check"]["generation"]
    assert quality["pass_rate"] == 0.75
    assert quality["accuracy"] is None
    cancelled = asyncio.run(CATALOG.call("cancel_finetune", {"job": str(job.id)}, context))
    assert not cancelled.isError, cancelled.structuredContent
    job.refresh_from_db()
    assert job.status == "cancelled"
    assert job.validation_runs.get().state == "completed"
    other = Project.objects.create(name="Not authorised", slug="not-authorised")
    foreign = FinetuningJob.objects.create(
        project=other, dataset=Dataset.objects.create(project=other)
    )
    denied = asyncio.run(
        CATALOG.call("inspect_training_progress", {"job": str(foreign.id)}, context)
    )
    assert denied.isError


@pytest.mark.django_db(transaction=True)
def test_agent_evidence_is_lossless_or_returns_an_explicit_size_error():
    user = User.objects.create_user(email=f"evidence-{uuid.uuid4()}@example.com", password="test")
    project = Project.objects.create(name="Evidence")
    ProjectMembership.objects.create(user=user, project=project)
    job = FinetuningJob.objects.create(
        project=project, dataset=Dataset.objects.create(project=project)
    )
    context = MCPContext(
        user=user,
        project=project,
        token=APIToken(scope={"scope": "project", "permission": ["read"]}),
    )
    rows = [
        {
            "row": 0,
            "input": "x" * 15000,
            "output": "y" * 15000,
            "reference": "z",
            "status": "completed",
        }
    ]
    training_monitoring.ingest(
        job,
        {
            "checks": [
                {
                    "key": "1:development:0",
                    "attempt": 1,
                    "stream": "development",
                    "step": 0,
                    "state": "completed",
                    "policy_fingerprint": "a" * 64,
                    "sample_fingerprint": "b" * 64,
                    "artifact": {"sha256": fingerprint(rows), "examples": rows},
                }
            ]
        },
    )
    result = asyncio.run(
        CATALOG.call(
            "inspect_training_progress",
            {
                "job": str(job.pk),
                "check": str(job.validation_runs.get().pk),
            },
            context,
        )
    )
    assert not result.isError, result.structuredContent
    assert result.structuredContent["progress"]["items"] == rows


@pytest.mark.django_db(transaction=True)
def test_frozen_monitoring_rows_are_paged_without_provider_access():
    user = User.objects.create_user(email=f"probe-{uuid.uuid4()}@example.com", password="test")
    project = Project.objects.create(name="Frozen monitoring")
    job = FinetuningJob.objects.create(
        project=project, dataset=Dataset.objects.create(project=project)
    )
    policy = resolve_policy({"loss_sample": 5}, has_development=True, provider="modal")
    job.hyperparameters = {"monitoring": policy}
    job.save()
    rows = [
        row_identity({"key": str(i), "input_ids": [1, i], "labels": [-100, i]}) for i in range(8)
    ]
    plan = freeze_plan(policy, rows, rows)
    training_monitoring.save_plan(job, plan)
    training_monitoring.save_plan(job, plan)
    context = MCPContext(
        user=user,
        project=project,
        token=APIToken(scope={"scope": "project", "permission": ["read"]}),
    )
    with patch(
        "modal.Function.from_name", side_effect=AssertionError("inspection invoked provider")
    ):
        result = asyncio.run(
            CATALOG.call(
                "inspect_training_progress",
                {"job": str(job.id), "probe": "development", "limit": 2},
                context,
            )
        )
    assert not result.isError, result.structuredContent
    assert (
        result.structuredContent["progress"]["items"]
        == plan["probes"]["development"]["identities"][:2]
    )
    assert result.structuredContent["progress"]["next_offset"] == 2
