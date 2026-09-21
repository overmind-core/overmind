from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pandas as pd
import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from mcp_fixtures import training_setup

from modal_shared.preparation import preparation_failure
from overbae.models import (
    APIToken,
    Cell,
    Dataset,
    DeployedModel,
    FinetuningJob,
    Project,
    ProjectMembership,
    TrainingPreparation,
    User,
)
from overbae.services.datasets import paths, review, store
from overbae.services.finetuning_validator import ValidationResult
from overbae.services.mcp import tools_finetuning
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext, bind_context
from overbae.services.mcp.resources import read_resource

pytestmark = pytest.mark.django_db(transaction=True)


def _context(*, permission: str | list[str] = "read") -> MCPContext:
    user = User.objects.create_user(
        email=f"mcp-ft-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="Fine tuning", slug=f"ft-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    permissions = [permission] if isinstance(permission, str) else permission
    token = APIToken(scope={"scope": "project", "permission": permissions})
    return MCPContext(user=user, token=token, project=project)


def _call(name: str, arguments: dict, context: MCPContext):
    return asyncio.run(CATALOG.call(name, arguments, context))


def _ok_cell(dataset, *, intent, rows=2, title="source", position=0, active=True, fits=True):
    cell = Cell.objects.create(
        dataset=dataset,
        position=position,
        title=title,
        state=Cell.State.OK,
        rows=rows,
        fingerprint=f"fp-{position}-{uuid.uuid4().hex[:8]}",
        intent_report={intent: {"ok": fits, "reason": "" if fits else "not a fit"}},
        capability_report={"ok": True, "reason": ""},
        stats={"num_examples": rows, "max_token_length": 8},
    )
    if active:
        dataset.active = cell
        dataset.save(update_fields=["active"])
    path = paths.cell_path(dataset.id, cell.id)
    store.write_frame(
        path,
        pd.DataFrame(
            [
                {
                    "source_row": i,
                    "messages": [
                        {"role": "user", "content": f"q{i}"},
                        {"role": "assistant", "content": f"a{i}"},
                    ],
                }
                if intent == "train"
                else {"source_row": i, "input": f"q{i}", "expected_output": f"a{i}"}
                for i in range(rows)
            ]
        ),
    )
    cell.fingerprint = store.file_sha256(path)
    cell.save(update_fields=["fingerprint"])
    review.record_quality(
        dataset,
        cell,
        [
            {
                "name": name,
                "result": "pass",
                "rows_checked": rows,
                "evidence": "Controlled test fixture.",
            }
            for name in review.REQUIRED_CHECKS
        ],
        script="df = pd.DataFrame({'source_row': df.source_row, **{name: [True] * len(df) for name in ('task_alignment', 'input_evidence', 'answer_support', 'output_schema')}})",
    )
    return cell


def test_catalog_has_finetuning_tools_and_read_only_keys_hide_writes():
    names = {
        "check_finetune_readiness",
        "prepare_training_data",
        "estimate_finetune",
        "start_finetune",
        "retry_deployment",
        "set_active_model",
        "set_benchmark_model",
        "run_inference",
        "get_model_swap_prompt",
    }
    assert names <= {definition.name for definition in CATALOG.definitions()}
    read_only = {"check_finetune_readiness", "estimate_finetune", "get_model_swap_prompt"}
    assert names - read_only >= {
        tool.name for tool in CATALOG.tools(frozenset({"write"})) if tool.name in names
    }
    assert (names - read_only).isdisjoint(tool.name for tool in CATALOG.tools(frozenset({"read"})))
    assert {tool.name for tool in CATALOG.tools(frozenset({"read"}))} >= {
        "check_finetune_readiness",
        "estimate_finetune",
    }


def test_exact_preparation_has_pollable_project_scoped_receipt(settings, monkeypatch):
    settings.FINETUNING_BACKEND = "modal"
    context = _context(permission=["read", "write", "train"])
    dataset = frozen_dataset(context.project, TRAIN_ROWS, contract="train")
    monkeypatch.setattr(tools_finetuning.inspect_preparation, "delay", lambda *a: None)
    result = _call(
        "prepare_training_data",
        {"dataset": str(dataset.id), "base_model": "Qwen/Qwen3-8B", "context_length": 4096},
        context,
    )
    assert not result.isError, result.structuredContent
    receipt = result.structuredContent["job"]
    assert receipt["kind"] == "training_preparation"
    poll = _call("get_job", {"kind": receipt["kind"], "id": receipt["id"]}, context)
    assert not poll.isError, poll.structuredContent

    async def resource():
        with bind_context(context):
            contents = list(
                await read_resource(f"overmind://jobs/training_preparation/{receipt['id']}")
            )
        return json.loads(contents[0].content)

    assert asyncio.run(resource())["status"] == "queued"
    failure = preparation_failure("worker_out_of_date")
    TrainingPreparation.objects.filter(pk=receipt["id"]).update(
        state="failed", report=failure, error=failure["error"]
    )
    failed = _call("get_job", {"kind": receipt["kind"], "id": receipt["id"]}, context)
    assert not failed.isError
    assert failed.structuredContent["status"] == "failed"
    assert failed.structuredContent["job_error"] == failure["error"]
    assert failed.structuredContent["progress"] == failure
    assert asyncio.run(resource())["error"] == failure["error"]
    other = _context()
    denied = _call("get_job", {"kind": receipt["kind"], "id": receipt["id"]}, other)
    assert denied.isError


def test_readiness_rejects_wrong_intent():
    context = _context()
    wrong_intent = Dataset.objects.create(
        project=context.project,
        name="Eval-shaped",
        intent=Dataset.Intent.EVAL,
    )
    result = _call("check_finetune_readiness", {"dataset": str(wrong_intent.id)}, context)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "dataset_intent_mismatch"

    pending = Dataset.objects.create(
        project=context.project,
        name="Pending train",
        intent=Dataset.Intent.PENDING,
    )
    result = _call("estimate_finetune", {"dataset": str(pending.id), "base_model": "x"}, context)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "dataset_intent_mismatch"


def test_readiness_treats_legacy_ft_as_train():
    context = _context()
    dataset = Dataset.objects.create(
        project=context.project,
        name="trading-decision-ft-text",
        intent="ft",
    )
    result = _call("check_finetune_readiness", {"dataset": str(dataset.id)}, context)
    assert result.isError is False
    assert result.structuredContent["dataset"]["intent"] == "train"
    estimate = _call(
        "estimate_finetune",
        {"dataset": str(dataset.id), "base_model": "x"},
        context,
    )
    assert estimate.isError is True
    assert estimate.structuredContent["error"]["code"] == "finetune_not_ready"


def test_readiness_classifies_selected_capability_and_defers_to_data_for_none(monkeypatch):
    context = _context()
    capability, dataset, _, _ = training_setup(context)
    capability.description = "Write Python code."
    capability.save(update_fields=["description"])
    monkeypatch.setattr(
        "overbae.services.codebase.task_type.call_llm",
        lambda *args, **kwargs: ('{"task_type":"code_generation"}', {}),
    )
    selected = _call(
        "check_finetune_readiness",
        {"dataset": str(dataset.id), "capability": str(capability.id)},
        context,
    )
    unassigned = _call("check_finetune_readiness", {"dataset": str(dataset.id)}, context)

    assert not selected.isError, selected.structuredContent
    assert not unassigned.isError, unassigned.structuredContent
    assert selected.structuredContent["task_type"] == "code_generation"
    assert selected.structuredContent["task_type_source"] == "capability"
    assert unassigned.structuredContent["task_type_source"] == "heuristic"
    definition = next(
        item for item in CATALOG.definitions() if item.name == "check_finetune_readiness"
    )
    assert definition.cost_class == "llm"


def test_cross_project_references_are_not_resolved():
    context = _context()
    other = _context()
    dataset = Dataset.objects.create(
        project=other.project,
        name="Other train",
        intent=Dataset.Intent.TRAIN,
    )
    deployment = DeployedModel.objects.create(
        project=other.project,
        model_id="ft-other",
        status=DeployedModel.Status.READY,
    )
    estimate = _call(
        "estimate_finetune",
        {"dataset": str(dataset.id), "base_model": "Qwen/Qwen2.5-7B-Instruct"},
        context,
    )
    write_context = MCPContext(
        user=context.user,
        token=APIToken(scope={"scope": "project", "permission": ["write"]}),
        project=context.project,
    )
    active = _call(
        "set_active_model",
        {"capability": "missing", "deployment": str(deployment.id)},
        write_context,
    )
    assert estimate.structuredContent["error"]["code"] == "dataset_not_found"
    assert active.structuredContent["error"]["code"] == "capability_not_found"


def test_estimate_uses_existing_estimator_without_creating_a_job(monkeypatch):
    context = _context()
    dataset = Dataset.objects.create(
        project=context.project,
        name="Train",
        intent=Dataset.Intent.TRAIN,
    )
    cell = _ok_cell(dataset, intent="train", rows=4)
    calls = {}

    def estimate(dataset_id, **kwargs):
        calls.update(dataset_id=dataset_id, kwargs=kwargs)
        return {
            "cost_estimate": {"usd": 1.25},
            "time_estimate": {"seconds": 60, "human": "1 min"},
            "trained_tokens": 1000,
        }

    monkeypatch.setattr(tools_finetuning, "estimate_for_hyperparams", estimate)
    result = _call(
        "estimate_finetune",
        {
            "dataset": str(dataset.id),
            "base_model": "Qwen/Qwen2.5-7B-Instruct",
            "n_epochs": 4,
            "use_lora": False,
        },
        context,
    )
    assert result.isError is False, result.structuredContent
    assert calls == {
        "dataset_id": str(dataset.id),
        "kwargs": {
            "base_model": "Qwen/Qwen2.5-7B-Instruct",
            "n_epochs": 4,
            "use_lora": False,
            "cell": cell,
        },
    }
    assert result.structuredContent["trained_tokens"] == 1000
    assert result.structuredContent["cell"]["id"] == str(cell.id)
    assert result.structuredContent["cell"]["rows"] == 4
    assert not FinetuningJob.objects.filter(project=context.project).exists()


@pytest.mark.parametrize("capability_choice", ["selected", "omitted", "none"])
@pytest.mark.parametrize("unassigned_set", [False, True])
@pytest.mark.parametrize("disable_evals", [False, True])
def test_start_uses_serializer_and_worker_task(
    monkeypatch, capability_choice, unassigned_set, disable_evals
):
    context = _context(permission=["read", "write"])
    capability, train, _evaluation, _eval_set = training_setup(context)
    if unassigned_set:
        _eval_set.capability = None
        _eval_set.save(update_fields=["capability"])
    calls = {}
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
        lambda **kwargs: calls.update(kwargs=kwargs) or SimpleNamespace(id="celery-ft"),
    )

    result = _call(
        "start_finetune",
        {
            "dataset": str(train.id),
            **(
                {"capability": str(capability.id)}
                if capability_choice == "selected"
                else {"capability": None}
                if capability_choice == "none"
                else {}
            ),
            "base_model": "Qwen/Qwen2.5-7B-Instruct",
            **({"baseline_model": capability.model} if capability_choice == "selected" else {}),
            **(
                {
                    "eval_incumbent_before": False,
                    "eval_incumbent_after": False,
                    "eval_model_before": False,
                    "eval_model_after": False,
                }
                if disable_evals
                else {}
            ),
        },
        context,
    )
    assert result.isError is False, result.structuredContent
    job = FinetuningJob.objects.get(project=context.project)
    assert job.capability_id == (capability.id if capability_choice == "selected" else None)
    if capability_choice == "selected":
        assert job.baseline_model == capability.model
    assert job.eval_dataset_id == _evaluation.id
    assert job.eval_set_id == _eval_set.id
    if disable_evals:
        assert not any(
            (
                job.eval_incumbent_before,
                job.eval_incumbent_after,
                job.eval_model_before,
                job.eval_model_after,
            )
        )
    assert calls["kwargs"] == {"kwargs": {"job_id": str(job.id)}}
    assert result.structuredContent["finetune"]["resource"]["uri"] == (
        f"overmind://finetunes/{job.id}"
    )
    assert result.structuredContent["job"]["resource"]["uri"] == (
        f"overmind://jobs/finetune/{job.id}"
    )
    assert job.cell_id == train.active_cell.id
    assert result.structuredContent["cell"]["id"] == str(job.cell_id)
    train.active_cell.refresh_from_db()
    assert train.active_cell.used_at is not None


@pytest.mark.parametrize(
    "deployment_status",
    [DeployedModel.Status.FAILED, DeployedModel.Status.DELETED],
)
def test_retry_returns_resource_and_dispatches_for_recoverable_deployment(
    monkeypatch, deployment_status
):
    context = _context(permission=["read", "write"])
    _, train, _, _ = training_setup(context)
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
        status=deployment_status,
        model_id=f"ft-retry-{deployment_status}",
        error_message="previous deployment failure",
    )
    monkeypatch.setattr("overbae.api.credit_gate.require_credits", lambda _user: None)
    calls = {}
    monkeypatch.setattr(
        "overbae.tasks.model_deployment.register_finetuned_model.delay",
        lambda **kwargs: calls.update(kwargs=kwargs),
    )
    result = _call("retry_deployment", {"deployment": deployment.model_id}, context)
    assert result.isError is False, result.structuredContent
    assert calls == {}  # the database outbox does not depend on a broker acknowledgement
    deployment.refresh_from_db()
    assert deployment.status == DeployedModel.Status.QUEUED
    assert deployment.error_message == ""
    assert result.structuredContent["retry"]["kind"] == "deployment"
    assert result.structuredContent["retry"]["status"] == DeployedModel.Status.QUEUED
    assert result.structuredContent["retry"]["resource"]["uri"] == (
        f"overmind://jobs/deployment/{deployment.id}"
    )
    assert result.structuredContent["deployment"]["resource"]["uri"] == (
        f"overmind://deployments/{deployment.id}"
    )


@pytest.mark.parametrize(
    "deployment_status",
    [
        DeployedModel.Status.QUEUED,
        DeployedModel.Status.QUANTIZING,
        DeployedModel.Status.DEPLOYING,
        DeployedModel.Status.WARMING,
        DeployedModel.Status.READY,
        DeployedModel.Status.DELETING,
    ],
)
def test_retry_rejects_ready_or_in_flight_deployment(deployment_status):
    context = _context(permission=["read", "write"])
    train = Dataset.objects.create(
        project=context.project, name="Train", intent=Dataset.Intent.TRAIN
    )
    job = FinetuningJob.objects.create(
        project=context.project,
        dataset=train,
        base_model="Qwen/Qwen2.5-7B-Instruct",
        status=FinetuningJob.Status.SUCCEEDED,
    )
    deployment = DeployedModel.objects.create(
        project=context.project,
        finetuning_job=job,
        model_id=f"ft-no-retry-{deployment_status}",
        status=deployment_status,
    )

    result = _call("retry_deployment", {"deployment": str(deployment.id)}, context)

    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "deployment_not_ready"


def test_retry_rejects_deployment_without_usable_finetune_job():
    context = _context(permission=["read", "write"])
    train = Dataset.objects.create(
        project=context.project, name="Train", intent=Dataset.Intent.TRAIN
    )
    job = FinetuningJob.objects.create(
        project=context.project,
        dataset=train,
        base_model="Qwen/Qwen2.5-7B-Instruct",
        status=FinetuningJob.Status.RUNNING,
    )
    deployment = DeployedModel.objects.create(
        project=context.project,
        finetuning_job=job,
        model_id="ft-no-checkpoint",
        status=DeployedModel.Status.FAILED,
    )

    result = _call("retry_deployment", {"deployment": str(deployment.id)}, context)

    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "finetune_not_ready"


def test_retry_remains_durable_when_broker_is_unavailable(monkeypatch):
    context = _context(permission=["read", "write"])
    _, train, _, _ = training_setup(context)
    job = FinetuningJob.objects.create(
        project=context.project,
        dataset=train,
        base_model="Qwen/Qwen2.5-7B-Instruct",
        status=FinetuningJob.Status.SUCCEEDED,
    )
    deployment = DeployedModel.objects.create(
        project=context.project,
        finetuning_job=job,
        model_id="ft-dispatch-failure",
        status=DeployedModel.Status.FAILED,
        error_message="previous failure",
    )
    monkeypatch.setattr("overbae.api.credit_gate.require_credits", lambda _user: None)

    job.remote_job_id = "remote"
    job.save(update_fields=["remote_job_id"])

    def fail_dispatch(**_kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        "overbae.tasks.model_deployment.register_finetuned_model.delay",
        fail_dispatch,
    )

    result = _call("retry_deployment", {"deployment": str(deployment.id)}, context)

    assert result.isError is False
    deployment.refresh_from_db()
    assert deployment.status == DeployedModel.Status.QUEUED
    assert deployment.deployment_stage == "base"
    assert deployment.error_message == ""


def test_set_active_model_validates_ready_same_project_and_clear(monkeypatch):
    context = _context(permission=["read", "write"])
    capability, train, _, _ = training_setup(context)
    deployment = DeployedModel.objects.create(
        project=context.project,
        model_id="ft-active",
        status=DeployedModel.Status.READY,
    )
    set_result = _call(
        "set_active_model",
        {"capability": str(capability.id), "deployment": str(deployment.id)},
        context,
    )
    capability.refresh_from_db()
    assert set_result.isError is False, set_result.structuredContent
    assert capability.active_model_id == deployment.id

    clear_result = _call(
        "set_active_model", {"capability": str(capability.id), "deployment": None}, context
    )
    capability.refresh_from_db()
    assert clear_result.isError is False, clear_result.structuredContent
    assert clear_result.structuredContent["cleared"] is True
    assert capability.active_model_id is None


def test_benchmark_tool_and_capability_resource_preserve_serving():
    context = _context(permission=["read", "write", "train"])
    capability, train, _, _ = training_setup(context)
    job = FinetuningJob.objects.create(
        project=context.project, capability=capability, dataset=train, base_model="Qwen/Qwen3-8B"
    )
    benchmark = DeployedModel.objects.create(
        project=context.project, finetuning_job=job, model_id="ft-benchmark", status="ready"
    )
    live = DeployedModel.objects.create(project=context.project, model_id="ft-live", status="ready")
    capability.active_model = live
    capability.save(update_fields=["active_model"])
    result = _call(
        "set_benchmark_model",
        {"capability": str(capability.id), "deployment": str(benchmark.id)},
        context,
    )
    assert not result.isError, result.structuredContent
    assert result.structuredContent["source"] == "trained"
    assert result.structuredContent["model_id"] == benchmark.model_id
    capability.refresh_from_db()
    assert capability.benchmark_model_id == benchmark.id
    assert capability.active_model_id == live.id

    async def resource():
        with bind_context(context):
            contents = list(await read_resource(f"overmind://capabilities/{capability.id}"))
        return json.loads(contents[0].content)

    state = asyncio.run(resource())
    assert state["benchmark_model"]["id"] == str(benchmark.id)
    assert [candidate["id"] for candidate in state["benchmark_candidates"]] == [str(benchmark.id)]
    result = _call("set_benchmark_model", {"capability": str(capability.id)}, context)
    assert not result.isError, result.structuredContent
    assert result.structuredContent["source"] == "codebase"
    assert result.structuredContent["model_id"] == capability.model
    capability.refresh_from_db()
    assert capability.benchmark_model_id is None
    assert capability.active_model_id == live.id


def test_benchmark_tool_rejects_infrastructure_and_foreign_projects():
    context = _context(permission=["read", "write", "train"])
    capability, _, _, _ = training_setup(context)
    foreign_context = _context()
    for project in (context.project, foreign_context.project):
        deployment = DeployedModel.objects.create(
            project=project, model_id=f"base-{project.id}", status="ready"
        )
        result = _call(
            "set_benchmark_model",
            {"capability": str(capability.id), "deployment": str(deployment.id)},
            context,
        )
        assert result.isError
    capability.refresh_from_db()
    assert capability.benchmark_model_id is None


def test_run_inference_redacts_service_errors(monkeypatch):
    context = _context(permission=["read", "write"])
    deployment = DeployedModel.objects.create(
        project=context.project,
        model_id="ft-infer",
        status=DeployedModel.Status.READY,
    )
    monkeypatch.setattr("overbae.api.credit_gate.require_credits", lambda _user: None)
    monkeypatch.setattr(
        tools_finetuning,
        "chat_with_deployed_model",
        lambda **_kwargs: {"error": "provider secret body"},
    )
    result = _call(
        "run_inference",
        {
            "deployment": str(deployment.id),
            "messages": [{"role": "user", "content": "hello"}],
        },
        context,
    )
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "inference_failed"
    assert "provider secret" not in str(result.structuredContent)


def test_start_rejects_credential_shaped_hyperparameter_keys():
    context = _context(permission=["read", "write"])
    capability, train, _, _ = training_setup(context)
    result = _call(
        "start_finetune",
        {
            "dataset": str(train.id),
            "capability": str(capability.id),
            "base_model": "Qwen/Qwen2.5-7B-Instruct",
            "hyperparameters": {"provider_api_key": "not accepted"},
        },
        context,
    )
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "invalid_input"
    assert not FinetuningJob.objects.filter(project=context.project).exists()


def test_model_swap_prompt_returns_prompt_and_capability_refs(monkeypatch):
    context = _context(permission=["read", "write"])
    capability, train, _, _ = training_setup(context)
    job = FinetuningJob.objects.create(
        project=context.project,
        capability=capability,
        dataset=train,
        base_model="Qwen/Qwen2.5-7B-Instruct",
        status=FinetuningJob.Status.SUCCEEDED,
    )
    DeployedModel.objects.create(
        project=context.project,
        finetuning_job=job,
        model_id="ft-pr",
        status=DeployedModel.Status.READY,
    )
    monkeypatch.setattr(
        "overbae.services.model_swap_prompt.model_swap_prompt_for_job",
        lambda _job, pin=False: (
            {
                "prompt": "Point the client at the new model.",
                "pin": pin,
                "capability_id": str(capability.id),
                "capability_name": capability.name,
                "old_model": "gpt-4o-mini",
                "new_model": "ft-pr",
            },
            None,
        ),
    )
    result = _call("get_model_swap_prompt", {"finetune": str(job.id)}, context)
    assert result.isError is False, result.structuredContent
    body = result.structuredContent
    assert body["prompt"] == "Point the client at the new model."
    assert body["old_model"] == "gpt-4o-mini"
    assert body["new_model"] == "ft-pr"
    assert body["capability_id"] == str(capability.id)


def test_model_swap_prompt_reports_why_it_is_unavailable(monkeypatch):
    context = _context(permission=["read", "write"])
    capability, train, _, _ = training_setup(context)
    job = FinetuningJob.objects.create(
        project=context.project,
        capability=capability,
        dataset=train,
        base_model="Qwen/Qwen2.5-7B-Instruct",
        status=FinetuningJob.Status.RUNNING,
    )
    monkeypatch.setattr(
        "overbae.services.model_swap_prompt.model_swap_prompt_for_job",
        lambda _job, pin=False: (None, "Only successfully trained models can be shipped."),
    )
    result = _call("get_model_swap_prompt", {"finetune": str(job.id)}, context)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "model_swap_prompt_not_ready"


def test_deployment_resource_has_url_and_bounded_metrics():
    context = _context()
    deployment = DeployedModel.objects.create(
        project=context.project,
        model_id="ft-resource",
        status=DeployedModel.Status.READY,
        inference_url="https://inference.example/models/ft-resource",
    )

    async def read():
        with bind_context(context):
            values = await read_resource(f"overmind://deployments/{deployment.id}")
        return values[0].content

    import json

    resource = json.loads(asyncio.run(read()))
    assert resource["inference_url"] == deployment.inference_url
    assert resource["metrics"]["request_count"] == 0


def test_finetune_resource_bounds_progress_without_checkpoint_urls():
    context = _context()
    job = FinetuningJob.objects.create(
        project=context.project,
        dataset=Dataset.objects.create(
            project=context.project,
            name="Train",
            intent=Dataset.Intent.TRAIN,
        ),
        base_model="Qwen/Qwen2.5-7B-Instruct",
        progress={
            "percent": 50,
            "metrics": {"loss": [1.0, 0.5]},
            "checkpoints": [{"url": "https://signed.example/checkpoint"}],
        },
    )

    async def read():
        with bind_context(context):
            values = await read_resource(f"overmind://finetunes/{job.id}")
        return values[0].content

    import json

    resource = json.loads(asyncio.run(read()))
    assert resource["progress"]["percent"] == 50
    assert resource["loss"] == [1.0, 0.5]
    assert "checkpoints" not in resource["progress"]
    assert "signed.example" not in json.dumps(resource)


def test_readiness_reports_chosen_cell_rows_and_contract_failure(monkeypatch):
    context = _context()
    dataset = Dataset.objects.create(
        project=context.project, name="Train", intent=Dataset.Intent.TRAIN
    )
    cell = _ok_cell(dataset, intent="train", rows=7, fits=False)
    monkeypatch.setattr(
        tools_finetuning,
        "finetune_prerequisite_report",
        lambda *_args, **_kwargs: {
            "missing": [],
            "catalog": {},
            "n_candidates": 0,
            "recommendations": [],
            "has_tool_calling": False,
            "excluded": [],
        },
    )
    result = _call("check_finetune_readiness", {"dataset": str(dataset.id)}, context)
    assert result.isError is False
    output = result.structuredContent
    assert output["ready"] is False
    assert output["dataset"]["cell"]["id"] == str(cell.id)
    assert output["dataset"]["cell"]["rows"] == 7
    assert output["dataset"]["cell"]["fits"] is False
    assert any("training dataset" in item for item in output["missing"])


def test_start_uses_explicit_cell_not_active(monkeypatch):
    context = _context(permission=["read", "write"])
    capability, train, _evaluation, _eval_set = training_setup(context)
    extra = _ok_cell(train, intent="train", rows=9, title="shaped", position=1, active=False)
    called = {}

    def fake_launch(**kwargs):
        called.update(kwargs)
        return FinetuningJob.objects.create(
            project=context.project,
            dataset=train,
            capability=capability,
            cell=kwargs["cell"],
            base_model=kwargs["base_model"],
            name="job",
            status=FinetuningJob.Status.QUEUED,
        )

    monkeypatch.setattr(tools_finetuning, "launch_finetune", fake_launch)
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
    result = _call(
        "start_finetune",
        {
            "dataset": str(train.id),
            "capability": str(capability.id),
            "base_model": "Qwen/Qwen2.5-7B-Instruct",
            "cell": str(extra.id),
            "baseline_model": "ft-selected-benchmark",
        },
        context,
    )
    assert result.isError is False, result.structuredContent
    assert called["cell"].id == extra.id
    assert called["baseline_model"] == "ft-selected-benchmark"
    extra.refresh_from_db()
    assert extra.used_at is None  # The mocked launch skips the atomic creation/freeze service.
    assert result.structuredContent["cell"]["id"] == str(extra.id)
