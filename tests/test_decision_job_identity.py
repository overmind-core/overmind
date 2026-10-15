import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest
from conftest import frozen_dataset
from django.apps import apps
from django.db import connection
from factories import auth_client, make_project, make_user
from mcp.shared.exceptions import McpError

from overbae.models import APIToken, DecisionJob, FinetuningJob
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext, bind_context
from overbae.services.mcp.resources import read_resource
from overbae.services.training_preparation import begin_preparation
from overbae.tasks import finetuning


def decision_dataset(project):
    return frozen_dataset(
        project,
        [
            {
                "decision": {
                    "state": "A coin landed heads.",
                    "question": "Which side?",
                    "kind": "choice",
                    "options": ["heads", "tails"],
                    "target_probabilities": [1.0, 0.0],
                }
            },
            {
                "decision": {
                    "state": "A coin landed tails.",
                    "question": "Which side?",
                    "kind": "choice",
                    "options": ["heads", "tails"],
                    "target_probabilities": [0.0, 1.0],
                }
            },
        ],
        contract="train",
    )


@pytest.mark.django_db(transaction=True)
def test_decision_identity_reuses_the_exact_training_run(monkeypatch, settings):
    settings.FINETUNING_BACKEND = "modal"
    monkeypatch.setenv("MODAL_ENVIRONMENT", "test")
    monkeypatch.setattr(
        finetuning.run_finetuning,
        "apply_async",
        lambda **_: SimpleNamespace(id="queued-task"),
    )
    user = make_user()
    project = make_project(member=user)
    dataset = decision_dataset(project)
    client = auth_client(user)
    catalog = client.get("/api/decision-models/")
    assert catalog.status_code == 200
    entries = {entry["id"]: entry for entry in catalog.data}
    assert {entry["family"] for entry in entries.values()} == {"clef", "d3", "d1"}
    assert entries["Cloudflare/clef-flash"]["training_available"]
    assert not entries["vllm-sr/d3-edge"]["training_available"]
    payload = {
        "project": str(project.id),
        "model": "Cloudflare/clef-flash",
        "dataset": str(dataset.id),
        "request_key": "decision-run-1",
        "validation_enabled": False,
        "name": "Coin decision trial",
        "use_case": "Binary choice from a short state",
    }
    first = client.post("/api/decision-jobs/", payload, format="json")
    second = client.post("/api/decision-jobs/", payload, format="json")

    assert first.status_code == second.status_code == 202, (first.data, second.data)
    assert first.data["id"] == second.data["id"]
    assert first.data["training_run"]["id"] == second.data["training_run"]["id"]
    assert DecisionJob.objects.count() == 1
    assert FinetuningJob.objects.count() == 1
    run = DecisionJob.objects.get(pk=first.data["id"]).finetuning_job
    assert run.cell_id == dataset.active_cell.id
    assert run.base_model == "Cloudflare/clef-flash"
    assert run.name == "Coin decision trial"
    assert run.use_case == "Binary choice from a short state"
    assert run.requested_configuration["contract"]["inference_contract"] == "decision"
    assert first.data["status"] == run.status
    listed = client.get("/api/decision-jobs/")
    assert listed.status_code == 200
    assert listed.data["results"][0]["id"] == first.data["id"]
    assert listed.data["results"][0]["training_run"]["decision_job_id"] == first.data["id"]
    context = MCPContext(
        user=user,
        project=project,
        token=APIToken(scope={"scope": "project", "permission": ["read", "write"]}),
    )
    mcp_catalog = asyncio.run(CATALOG.call("list_decision_training_models", {}, context))
    assert not mcp_catalog.isError, mcp_catalog.structuredContent
    assert {entry["family"] for entry in mcp_catalog.structuredContent["models"]} == {
        "clef",
        "d3",
        "d1",
    }
    receipt = asyncio.run(
        CATALOG.call("get_job", {"kind": "decision_job", "id": first.data["id"]}, context)
    )
    assert not receipt.isError, receipt.structuredContent
    assert receipt.structuredContent["details"]["finetuning_job"] == str(run.id)
    progress = asyncio.run(
        CATALOG.call("inspect_training_progress", {"job": first.data["id"]}, context)
    )
    assert not progress.isError, progress.structuredContent

    async def read_decision_resource(bound_context):
        with bind_context(bound_context):
            contents = list(await read_resource(f"overmind://jobs/decision_job/{first.data['id']}"))
        return json.loads(contents[0].content)

    resource = asyncio.run(read_decision_resource(context))
    assert resource["id"] == first.data["id"]
    assert resource["finetuning_job"] == str(run.id)

    estimate = asyncio.run(
        CATALOG.call(
            "estimate_finetune",
            {
                "dataset": str(dataset.id),
                "base_model": "Cloudflare/clef-flash",
                "validation_enabled": False,
            },
            context,
        )
    )
    assert not estimate.isError, estimate.structuredContent
    assert estimate.structuredContent["training_contract"]["inference_contract"] == "decision"

    launched = asyncio.run(
        CATALOG.call(
            "start_decision_job",
            {
                "dataset": str(dataset.id),
                "base_model": "Cloudflare/clef-flash",
                "request_key": "decision-run-mcp",
                "validation_enabled": False,
            },
            context,
        )
    )
    assert not launched.isError, launched.structuredContent
    assert launched.structuredContent["decision_job"]["kind"] == "decision_job"
    replay = asyncio.run(
        CATALOG.call(
            "start_decision_job",
            {
                "dataset": str(dataset.id),
                "base_model": "Cloudflare/clef-flash",
                "request_key": "decision-run-mcp",
                "validation_enabled": False,
            },
            context,
        )
    )
    assert not replay.isError, replay.structuredContent
    assert (
        replay.structuredContent["decision_job"]["id"]
        == launched.structuredContent["decision_job"]["id"]
    )
    assert DecisionJob.objects.count() == FinetuningJob.objects.count() == 2

    outsider = make_user()
    other_project = make_project(member=outsider)
    assert auth_client(outsider).get(f"/api/decision-jobs/{first.data['id']}/").status_code == 404
    other_context = MCPContext(
        user=outsider,
        project=other_project,
        token=APIToken(scope={"scope": "project", "permission": ["read"]}),
    )
    hidden = asyncio.run(
        CATALOG.call("get_job", {"kind": "decision_job", "id": first.data["id"]}, other_context)
    )
    assert hidden.isError
    with pytest.raises(McpError):
        asyncio.run(read_decision_resource(other_context))

    unsupported = client.post(
        "/api/decision-jobs/",
        {**payload, "model": "vllm-sr/d3-edge", "request_key": "decision-d3"},
        format="json",
    )
    assert unsupported.status_code == 400
    assert DecisionJob.objects.count() == 2

    wrong_hyperparameters = client.post(
        "/api/decision-jobs/",
        {**payload, "hyperparameters": []},
        format="json",
    )
    assert wrong_hyperparameters.status_code == 400


@pytest.mark.django_db(transaction=True)
def test_general_training_launch_exposes_decision_identity(monkeypatch, settings):
    settings.FINETUNING_BACKEND = "modal"
    monkeypatch.setattr(
        finetuning.run_finetuning,
        "apply_async",
        lambda **_: SimpleNamespace(id="queued-task"),
    )
    user = make_user()
    project = make_project(member=user)
    dataset = decision_dataset(project)
    client = auth_client(user)
    general_training = client.post(
        "/api/finetuning-jobs/",
        {
            "project": str(project.id),
            "dataset": str(dataset.id),
            "base_model": "Cloudflare/clef-flash",
            "request_key": "decision-general-training",
            "validation_enabled": False,
            "hyperparameters": {"training_type": {"type": "Lora"}},
        },
        format="json",
    )
    assert general_training.status_code == 201, general_training.data
    identity = DecisionJob.objects.get(finetuning_job_id=general_training.data["id"])
    assert general_training.data["decision_job_id"] == str(identity.id)


@pytest.mark.django_db(transaction=True)
def test_decision_preparation_creates_the_same_training_identity(settings):
    settings.FINETUNING_BACKEND = "modal"
    user = make_user()
    project = make_project(member=user)
    dataset = frozen_dataset(
        project,
        [
            {
                "decision": {
                    "state": "A request needs review.",
                    "question": "Approve?",
                    "kind": "choice",
                    "options": ["yes", "no"],
                    "target_probabilities": [1.0, 0.0],
                }
            }
        ],
        contract="train",
    )
    preparation = begin_preparation(dataset.active_cell, "Cloudflare/clef-flash", 32768, user=user)
    job = preparation.training_jobs.get()
    assert job.status == FinetuningJob.Status.PREPROCESSING
    assert job.decision_job.finetuning_job_id == job.id
    preparation_response = auth_client(user).get(f"/api/training-preparations/{preparation.id}/")
    assert preparation_response.status_code == 200
    assert preparation_response.data["decision_job"] == str(job.decision_job.id)
    context = MCPContext(
        user=user,
        project=project,
        token=APIToken(scope={"scope": "project", "permission": ["read", "write"]}),
    )
    prepared = asyncio.run(
        CATALOG.call(
            "prepare_training_data",
            {
                "dataset": str(dataset.id),
                "base_model": "Cloudflare/clef-flash",
                "context_length": 32768,
            },
            context,
        )
    )
    assert not prepared.isError, prepared.structuredContent
    assert prepared.structuredContent["decision_job"]["id"] == str(job.decision_job.id)


@pytest.mark.django_db(transaction=True)
def test_existing_native_training_run_receives_decision_identity():
    user = make_user()
    project = make_project(member=user)
    dataset = decision_dataset(project)
    run = FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        cell=dataset.active_cell,
        base_model="OvermindLab/qwen-3.5-0.8B-decision",
        hyperparameters={"objective": "decision_cross_entropy"},
    )
    migration = importlib.import_module("overbae.migrations.0045_decision_job_identity")
    migration.attach_existing_decision_runs(apps, SimpleNamespace(connection=connection))
    identity = DecisionJob.objects.get(finetuning_job=run)
    assert identity.created_at == run.created_at
