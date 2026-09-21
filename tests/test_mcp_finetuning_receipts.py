from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from mcp_fixtures import training_setup

from overbae.models import (
    APIToken,
    Dataset,
    DeployedModel,
    FinetuningJob,
    Project,
    ProjectMembership,
    User,
)
from overbae.services.finetuning_validator import ValidationResult
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db(transaction=True)


def _context() -> MCPContext:
    user = User.objects.create_user(
        email=f"mcp-receipt-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="Fine tuning", slug=f"receipt-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    token = APIToken(scope={"scope": "project", "permission": ["read", "write"]})
    return MCPContext(user=user, token=token, project=project)


def _call(name: str, arguments: dict, context: MCPContext):
    return asyncio.run(CATALOG.call(name, arguments, context))


def test_start_finetune_job_receipt_has_kind_and_preserves_reference(monkeypatch):
    from overbae.services.mcp import tools_finetuning

    context = _context()
    capability, train, _evaluation, _eval_set = training_setup(context)
    monkeypatch.setattr(
        tools_finetuning,
        "validate_dataset",
        lambda *_args, **_kwargs: ValidationResult(True, "conversational", 1),
    )
    monkeypatch.setattr(
        tools_finetuning,
        "stamp_hyperparameters_for_model",
        lambda *_args: {"training_type": {"type": "Lora"}},
    )
    monkeypatch.setattr("overbae.api.credit_gate.require_credits", lambda _user: None)
    monkeypatch.setattr("overbae.services.plan_limits.require_plan_quota", lambda *_args: None)
    monkeypatch.setattr(
        "overbae.tasks.finetuning.run_finetuning.apply_async",
        lambda **_kwargs: SimpleNamespace(id="celery-ft"),
    )

    result = _call(
        "start_finetune",
        {
            "dataset": str(train.id),
            "capability": str(capability.id),
            "base_model": "Qwen/Qwen2.5-7B-Instruct",
        },
        context,
    )

    assert result.isError is False, result.structuredContent
    job = FinetuningJob.objects.get(project=context.project)
    receipt = result.structuredContent["job"]
    assert receipt["kind"] == "finetune_job"
    assert receipt["id"] == str(job.id)
    assert receipt["status"] == job.status
    assert receipt["name"] == job.name
    assert receipt["resource"]["uri"] == f"overmind://jobs/finetune/{job.id}"
    assert result.structuredContent["finetune"]["resource"]["uri"] == (
        f"overmind://finetunes/{job.id}"
    )


def test_retry_deployment_returns_named_deployment_job_receipt(monkeypatch):
    context = _context()
    train = Dataset.objects.create(
        project=context.project,
        name="Train",
        intent=Dataset.Intent.TRAIN,
    )
    job = FinetuningJob.objects.create(
        project=context.project,
        dataset=train,
        base_model="Qwen/Qwen2.5-7B-Instruct",
        status=FinetuningJob.Status.SUCCEEDED,
        remote_job_id="remote",
    )
    deployment = DeployedModel.objects.create(
        project=context.project,
        finetuning_job=job,
        model_id="ft-receipt",
        status=DeployedModel.Status.FAILED,
    )
    monkeypatch.setattr("overbae.api.credit_gate.require_credits", lambda _user: None)
    monkeypatch.setattr(
        "overbae.tasks.model_deployment.register_finetuned_model.delay",
        lambda **_kwargs: None,
    )

    result = _call("retry_deployment", {"deployment": str(deployment.id)}, context)

    assert result.isError is False, result.structuredContent
    receipt = result.structuredContent["retry"]
    assert set(receipt) == {"kind", "id", "status", "resource"}
    assert receipt["kind"] == "deployment"
    assert receipt["id"] == str(deployment.id)
    assert receipt["status"] == DeployedModel.Status.QUEUED
    assert receipt["resource"]["uri"] == f"overmind://jobs/deployment/{deployment.id}"
    assert result.structuredContent["deployment"]["resource"]["uri"] == (
        f"overmind://deployments/{deployment.id}"
    )
    assert [link["uri"] for link in result.structuredContent["resource_links"]] == [
        f"overmind://deployments/{deployment.id}",
        f"overmind://jobs/deployment/{deployment.id}",
    ]
