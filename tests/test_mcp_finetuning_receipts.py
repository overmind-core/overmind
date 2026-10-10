from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from django.urls import reverse
from mcp_fixtures import training_setup
from rest_framework.test import APIClient

from overbae.api.credit_gate import PaymentRequired
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


@pytest.mark.parametrize("training_type", ["Lora", ["Lora"], 1, {"type": "unknown"}])
def test_start_rejects_invalid_training_method_without_creating_job(monkeypatch, training_type):
    context = _context()
    capability, train, _, _ = training_setup(context)
    monkeypatch.setattr("overbae.api.credit_gate.require_credits", lambda _: None)
    monkeypatch.setattr("overbae.services.plan_limits.require_plan_quota", lambda *_: None)
    result = _call(
        "start_finetune",
        {
            "dataset": str(train.id),
            "capability": str(capability.id),
            "base_model": "Qwen/Qwen2.5-7B-Instruct",
            "hyperparameters": {"training_type": training_type},
        },
        context,
    )
    assert result.isError
    assert result.structuredContent["error"]["code"] == "finetune_invalid"
    assert "hyperparameters" in result.structuredContent["error"]["fields"]
    assert not FinetuningJob.objects.filter(project=context.project).exists()
    train.refresh_from_db()
    assert train.active_cell.used_at is None


@pytest.mark.parametrize("judge_model", ["", "gpt-5.6-luna"])
def test_start_finetune_job_receipt_has_kind_and_preserves_reference(monkeypatch, judge_model):
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
            "eval_judge_model": judge_model,
        },
        context,
    )

    assert result.isError is False, result.structuredContent
    job = FinetuningJob.objects.get(project=context.project)
    assert job.eval_judge_model == judge_model
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


@pytest.mark.parametrize("gate", ["_require_credits", "_require_training_quota"])
def test_recovering_a_launch_does_not_require_budget_for_another_job(monkeypatch, gate):
    from overbae.services.mcp import tools_finetuning
    from overbae.services.mcp.errors import MCPError

    context = _context()
    capability, train, _, _ = training_setup(context)
    monkeypatch.setattr("overbae.api.credit_gate.require_credits", lambda _: None)
    monkeypatch.setattr("overbae.services.plan_limits.require_plan_quota", lambda *_: None)
    dispatched = []

    def dispatch(**kwargs):
        dispatched.append(kwargs)
        return SimpleNamespace(id="one-provider-submission")

    monkeypatch.setattr("overbae.tasks.finetuning.run_finetuning.apply_async", dispatch)
    recipe = {
        "dataset": str(train.id),
        "cell": str(train.active_cell.id),
        "capability": str(capability.id),
        "base_model": "Qwen/Qwen2.5-7B-Instruct",
        "hyperparameters": {"training_type": {"type": "Lora"}, "learning_rate": 0.0001},
        "request_key": "recover-same-training",
    }
    first = _call("start_finetune", recipe, context)
    assert not first.isError, first.structuredContent

    def exhausted(_context):
        raise MCPError("insufficient_credits", "No budget for another job")

    monkeypatch.setattr(tools_finetuning, gate, exhausted)
    repeated = _call("start_finetune", recipe, context)
    assert not repeated.isError, repeated.structuredContent
    assert repeated.structuredContent["job"]["id"] == first.structuredContent["job"]["id"]
    assert len(dispatched) == 1
    changed = _call(
        "start_finetune",
        {**recipe, "hyperparameters": {**recipe["hyperparameters"], "learning_rate": 0.0002}},
        context,
    )
    assert changed.isError
    assert "request_key" in changed.structuredContent["error"]["fields"]
    new = _call("start_finetune", {**recipe, "request_key": "another-training"}, context)
    assert new.isError
    assert new.structuredContent["error"]["code"] == "insufficient_credits"
    assert FinetuningJob.objects.filter(project=context.project).count() == 1
    assert len(dispatched) == 1


def test_console_recovers_an_existing_job_after_credits_are_exhausted(monkeypatch):
    context = _context()
    _, train, _, _ = training_setup(context)
    monkeypatch.setattr("overbae.api.credit_gate.require_credits", lambda _: None)
    monkeypatch.setattr("overbae.services.plan_limits.require_plan_quota", lambda *_: None)
    dispatched = []

    def dispatch(**kwargs):
        dispatched.append(kwargs)
        return SimpleNamespace(id="console-submission")

    monkeypatch.setattr("overbae.tasks.finetuning.run_finetuning.apply_async", dispatch)
    client = APIClient()
    client.force_authenticate(context.user)
    recipe = {
        "project": str(context.project.id),
        "dataset": str(train.id),
        "cell": str(train.active_cell.id),
        "base_model": "Qwen/Qwen2.5-7B-Instruct",
        "hyperparameters": {"training_type": {"type": "Lora"}, "learning_rate": 0.0001},
        "eval_model_before": False,
        "eval_model_after": False,
        "eval_incumbent_before": False,
        "eval_incumbent_after": False,
        "request_key": "console-recovery",
    }
    url = reverse("finetuningjob-list")
    first = client.post(url, recipe, format="json")
    assert first.status_code == 201, first.data

    def exhausted(_user):
        raise PaymentRequired()

    monkeypatch.setattr("overbae.api.credit_gate.require_credits", exhausted)
    repeated = client.post(url, recipe, format="json")
    assert repeated.status_code == 201, repeated.data
    assert repeated.data["id"] == first.data["id"]
    new = client.post(url, {**recipe, "request_key": "new-console-job"}, format="json")
    assert new.status_code == 402, new.data
    assert len(dispatched) == 1
    assert FinetuningJob.objects.filter(project=context.project).count() == 1
