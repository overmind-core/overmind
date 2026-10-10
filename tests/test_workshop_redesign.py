import asyncio
import json
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from overbae.models import APIToken, Dataset, Project, ProjectMembership
from overbae.services.datasets import paths, store, use, workbench
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext, bind_context
from overbae.services.mcp.resources import read_resource
from tests.workshop_script_fixtures import (
    conversation_script,
    execute_fixture,
    retained_package,
    select_script,
)

pytestmark = pytest.mark.django_db(transaction=True)


def workspace():
    user = get_user_model().objects.create_user(email="workshop-builder@test.com", password="pw")
    project = Project.objects.create(name="Workshop", slug="workshop-redesign")
    ProjectMembership.objects.create(project=project, user=user)
    client = APIClient()
    client.force_authenticate(user)
    context = MCPContext(
        user=user,
        token=APIToken(
            scope={
                "scope": "project",
                "resourceIds": [str(project.pk)],
                "permission": ["read", "write"],
            }
        ),
        project=project,
    )
    return project, client, context


def call(context, tool, arguments):
    result = asyncio.run(CATALOG.call(tool, arguments, context))
    assert not result.isError, result
    return result.structuredContent


def test_native_agent_prepares_data_without_a_platform_agent():
    project, client, context = workspace()
    response = client.post(
        "/api/datasets/",
        {
            "project": str(project.pk),
            "name": "Support examples",
            "brief": "Prepare training examples",
            "intent": "train",
            "source": {
                "rows": [
                    {"question": "Where is my order?", "answer": "Check the tracking link."},
                    {
                        "question": "Can I return it?",
                        "answer": "Returns are accepted in 30 days.",
                    },
                ]
            },
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    dataset = Dataset.objects.get(pk=response.data["id"])
    source = dataset.active_cell
    recipe = call(
        context,
        "save_dataset_pipeline",
        {
            "dataset": str(dataset.pk),
            "name": "Conversation format",
            "request_key": "recipe-1",
            "package": retained_package(
                context.project, context.user, [conversation_script("question", "answer")]
            ),
        },
    )
    arguments = {
        "dataset": str(dataset.pk),
        "pipeline": recipe["pipeline"]["id"],
        "source_cell": str(source.pk),
        "source_fingerprint": source.fingerprint,
        "request_key": "run-1",
    }
    from overbae.services.datasets.workbench import execute

    with patch("overbae.tasks.datasets.execute_pipeline.delay") as dispatch:
        receipt = call(context, "run_dataset_pipeline", arguments)
    dispatch.assert_not_called()
    execute(receipt["run"]["id"], script_executor=execute_fixture)
    repeated = call(context, "run_dataset_pipeline", arguments)
    assert repeated["run"]["id"] == receipt["run"]["id"]
    job = call(context, "get_job", {"kind": "dataset_pipeline", "id": receipt["job"]["id"]})
    assert job["status"] == "completed"
    assert job["details"]["source_fingerprint"] == source.fingerprint

    async def read_receipt():
        with bind_context(context):
            content = list(await read_resource(receipt["job"]["resource"]["uri"]))
        return json.loads(content[0].content)

    assert asyncio.run(read_receipt())["output_cell"] == repeated["run"]["output_cell"]
    dataset.refresh_from_db()
    output = dataset.active_cell
    assert output.pk != source.pk
    assert output.rows == 2
    assert use.check(dataset, "train", cell=output) is not None
    assert (
        list(store.iter_rows(paths.cell_path(dataset.pk, source.pk)))[0]["question"]
        == "Where is my order?"
    )
    detail = client.get(f"/api/datasets/{dataset.pk}/workbench/")
    assert detail.status_code == 200
    assert detail.data["runs"][0]["output_cell"] == str(output.pk)
    assert detail.data["pipelines"][0]["fingerprint"]


@pytest.mark.parametrize(
    "steps",
    [
        [conversation_script("question", "answer")],
        [select_script(["question", "answer"])],
    ],
)
def test_pipeline_does_not_reinterpret_landed_parent_fields(steps):
    project, client, context = workspace()
    response = client.post(
        "/api/datasets/",
        {
            "project": str(project.pk),
            "name": "Landed parent metadata",
            "intent": "explore",
            "source": {
                "rows": [
                    {
                        "question": f"Question {i}",
                        "answer": f"Answer {i}",
                        "_overmind_parent_rows": [0],
                    }
                    for i in range(3)
                ]
            },
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    dataset = Dataset.objects.get(pk=response.data["id"])
    source = dataset.active_cell
    recipe = call(
        context,
        "save_dataset_pipeline",
        {
            "dataset": str(dataset.pk),
            "name": "Preserve identities",
            "request_key": "preserve-identities",
            "package": retained_package(context.project, context.user, steps),
        },
    )
    with patch("overbae.tasks.datasets.execute_pipeline.delay"):
        receipt = call(
            context,
            "run_dataset_pipeline",
            {
                "dataset": str(dataset.pk),
                "pipeline": recipe["pipeline"]["id"],
                "source_cell": str(source.pk),
                "source_fingerprint": source.fingerprint,
                "request_key": "preserve-identities",
            },
        )
    workbench.execute(receipt["run"]["id"], script_executor=execute_fixture)
    job = call(context, "get_job", {"kind": "dataset_pipeline", "id": receipt["job"]["id"]})
    assert job["status"] == "completed", job
    dataset.refresh_from_db()
    output = list(store.iter_rows(paths.cell_path(dataset.pk, dataset.active_cell.pk)))
    assert [row["source_row"] for row in output] == [0, 1, 2]
    assert [row["_overmind_provenance"]["parents"][0]["row"] for row in output] == [0, 1, 2]
    assert [row["answer"] for row in output] == [f"Answer {i}" for i in range(3)]
    original = list(store.iter_rows(paths.cell_path(dataset.pk, source.pk)))
    assert all(row["_overmind_parent_rows"] == [0] for row in original)


def test_pipeline_conflicts_and_project_isolation_preserve_the_source():
    project, client, context = workspace()
    response = client.post(
        "/api/datasets/",
        {
            "project": str(project.pk),
            "name": "Source",
            "intent": "explore",
            "source": {"rows": [{"text": "evidence"}]},
        },
        format="json",
    )
    dataset = Dataset.objects.get(pk=response.data["id"])
    source = dataset.active_cell
    recipe = call(
        context,
        "save_dataset_pipeline",
        {
            "dataset": str(dataset.pk),
            "name": "Projection",
            "request_key": "recipe",
            "package": retained_package(context.project, context.user, [select_script(["text"])]),
        },
    )
    result = asyncio.run(
        CATALOG.call(
            "run_dataset_pipeline",
            {
                "dataset": str(dataset.pk),
                "pipeline": recipe["pipeline"]["id"],
                "source_cell": str(source.pk),
                "source_fingerprint": "0" * 64,
                "request_key": "stale",
            },
            context,
        )
    )
    assert result.isError
    foreign = Project.objects.create(name="Other", slug="other-workshop")
    denied = asyncio.run(
        CATALOG.call(
            "inspect_dataset_workbench",
            {"dataset": str(dataset.pk)},
            MCPContext(user=context.user, token=context.token, project=foreign),
        )
    )
    assert denied.isError
    dataset.refresh_from_db()
    assert dataset.active_cell.pk == source.pk
    assert dataset.cells.count() == 1


def test_brief_only_dataset_stays_available_without_scheduling_intelligence():
    project, client, _ = workspace()
    response = client.post(
        "/api/datasets/",
        {"project": str(project.pk), "name": "New data", "brief": "Prepare a training dataset"},
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["state"] == "idle"


def test_failed_external_lineage_and_cancelled_runs_never_publish():
    from overbae.services.datasets.workbench import execute

    project, client, context = workspace()
    response = client.post(
        "/api/datasets/",
        {
            "project": str(project.pk),
            "name": "Bound source",
            "intent": "explore",
            "source": {"rows": [{"text": "Source evidence"}]},
        },
        format="json",
    )
    dataset = Dataset.objects.get(pk=response.data["id"])
    source = dataset.active_cell
    arguments = {
        "dataset": str(dataset.pk),
        "source_cell": str(source.pk),
        "source_fingerprint": source.fingerprint,
        "request_key": "external-invalid",
        "name": "External output",
        "provenance": "Authored by the coding agent. " * 100,
        "imported_rows": [{"text": "Changed", "_overmind_parent_rows": [99999]}],
    }
    receipt = call(context, "import_dataset_version", arguments)
    execute(receipt["run"]["id"], script_executor=execute_fixture)
    inspected = call(context, "inspect_dataset_workbench", {"dataset": str(dataset.pk)})
    assert inspected["runs"][0]["state"] == "failed"
    assert "parent row" in inspected["runs"][0]["error"].lower()
    recipe = call(
        context,
        "save_dataset_pipeline",
        {
            "dataset": str(dataset.pk),
            "name": "Projection",
            "request_key": "projection",
            "package": retained_package(context.project, context.user, [select_script(["text"])]),
        },
    )
    receipt = call(
        context,
        "run_dataset_pipeline",
        {
            "dataset": str(dataset.pk),
            "source_cell": str(source.pk),
            "source_fingerprint": source.fingerprint,
            "request_key": "cancel-me",
            "pipeline": recipe["pipeline"]["id"],
        },
    )
    cancelled = client.post(f"/api/datasets/{dataset.pk}/cancel/", {}, format="json")
    assert cancelled.status_code == 202
    execute(receipt["run"]["id"], script_executor=execute_fixture)
    dataset.refresh_from_db()
    assert dataset.cells.count() == 1 and dataset.active_cell.pk == source.pk
    assert dataset.pipeline_runs.get(pk=receipt["run"]["id"]).state == "cancelled"


def test_external_output_records_attribution_and_preserves_frozen_inputs():
    from overbae.services.datasets.workbench import execute

    project, client, context = workspace()
    response = client.post(
        "/api/datasets/",
        {
            "project": str(project.pk),
            "name": "Bound source",
            "intent": "eval",
            "source": {
                "rows": [
                    {
                        "input": "Question",
                        "expected_output": "Source answer",
                        "human_reviewed": True,
                    }
                ]
            },
        },
        format="json",
    )
    dataset = Dataset.objects.get(pk=response.data["id"])
    source = dataset.active_cell
    use.use(dataset, "eval", cell=source)
    identity = next(store.iter_rows(paths.cell_path(dataset.pk, source.pk)))["source_row"]
    args = {
        "dataset": str(dataset.pk),
        "source_cell": str(source.pk),
        "source_fingerprint": source.fingerprint,
        "request_key": "external-valid",
        "name": "Agent-authored answer",
        "provenance": "Local script revision abc123",
        "imported_rows": [
            {"input": "Question", "expected_output": "New answer", "source_row": identity}
        ],
    }
    receipt = call(context, "import_dataset_version", args)
    execute(receipt["run"]["id"], script_executor=execute_fixture)
    repeated = call(context, "import_dataset_version", args)
    assert repeated["run"]["id"] == receipt["run"]["id"]
    dataset.refresh_from_db()
    output = dataset.active_cell
    assert output.review["execution"] == "external_attributed"
    assert output.quality_report == {}
    assert not next(store.iter_rows(paths.cell_path(dataset.pk, output.pk))).get("human_reviewed")
    assert repeated["run"]["provenance"] == "Local script revision abc123"
    assert (
        next(store.iter_rows(paths.cell_path(dataset.pk, source.pk)))["expected_output"]
        == "Source answer"
    )
    conflict = asyncio.run(
        CATALOG.call("import_dataset_version", {**args, "name": "Different request"}, context)
    )
    assert conflict.isError
    selected = call(
        context, "update_dataset", {"dataset": str(dataset.pk), "active": str(source.pk)}
    )
    assert selected["dataset"] == str(dataset.pk)
    dataset.refresh_from_db()
    assert dataset.active_id == source.pk


def test_retired_agent_and_arbitrary_code_surfaces_are_not_available():
    project, client, context = workspace()
    response = client.post(
        "/api/datasets/",
        {"project": str(project.pk), "name": "Draft", "brief": "Prepare data"},
        format="json",
    )
    dataset_id = response.data["id"]
    for tool in ("message_dataset_agent", "run_dataset", "manage_dataset_workflow"):
        assert asyncio.run(CATALOG.call(tool, {"dataset": dataset_id}, context)).isError
    for path in ("chat", "run", "cells/00000000-0000-0000-0000-000000000001"):
        response = client.post(f"/api/datasets/{dataset_id}/{path}/", {}, format="json")
        assert response.status_code == 404
    for path in ("cells", "workflow"):
        assert (
            client.post(f"/api/datasets/{dataset_id}/{path}/", {}, format="json").status_code == 404
        )
    invalid = asyncio.run(
        CATALOG.call(
            "save_dataset_pipeline",
            {
                "dataset": dataset_id,
                "name": "Untrusted code",
                "request_key": "code",
                "steps": [{"operation": "python", "code": "import os"}],
            },
            context,
        )
    )
    assert invalid.isError


def test_large_external_artifact_is_project_scoped_and_retained():
    from overbae.services.datasets.workbench import execute

    project, client, context = workspace()
    response = client.post(
        "/api/datasets/",
        {
            "project": str(project.pk),
            "name": "Evidence",
            "intent": "explore",
            "source": {"rows": [{"text": "Original evidence"}]},
        },
        format="json",
    )
    dataset = Dataset.objects.get(pk=response.data["id"])
    source = dataset.active_cell
    identity = next(store.iter_rows(paths.cell_path(dataset.pk, source.pk)))["source_row"]
    artifact_response = client.post(
        "/api/datasets/",
        {
            "project": str(project.pk),
            "name": "External artifact",
            "intent": "explore",
            "source": {
                "rows": [
                    {"text": f"Derived {i}", "_overmind_parent_rows": [identity]}
                    for i in range(2100)
                ]
            },
        },
        format="json",
    )
    artifact = Dataset.objects.get(pk=artifact_response.data["id"]).active_cell
    receipt = call(
        context,
        "import_dataset_version",
        {
            "dataset": str(dataset.pk),
            "source_cell": str(source.pk),
            "source_fingerprint": source.fingerprint,
            "request_key": "large-artifact",
            "name": "External output",
            "provenance": "Local batch script",
            "artifact_cell": str(artifact.pk),
            "artifact_fingerprint": artifact.fingerprint,
        },
    )
    execute(receipt["run"]["id"], script_executor=execute_fixture)
    dataset.refresh_from_db()
    assert dataset.active_cell.rows == 2100
    assert dataset.pipeline_runs.get(pk=receipt["run"]["id"]).artifact_id == artifact.pk
    job = call(context, "get_job", {"kind": "dataset_pipeline", "id": receipt["job"]["id"]})
    assert job["details"]["artifact_cell"] == str(artifact.pk)
    assert job["details"]["artifact_fingerprint"] == artifact.fingerprint
    assert client.delete(f"/api/datasets/{artifact.dataset_id}/").status_code == 409


def test_invalid_recipes_are_rejected_and_metadata_updates_are_atomic():
    project, client, context = workspace()
    response = client.post(
        "/api/datasets/",
        {
            "project": str(project.pk),
            "name": "Original",
            "intent": "eval",
            "source": {"rows": [{"input": "Question", "expected_output": "Answer"}]},
        },
        format="json",
    )
    dataset = Dataset.objects.get(pk=response.data["id"])
    for step in ({"operation": []}, {"operation": "rename", "mapping": {"input": []}}):
        result = client.post(
            f"/api/datasets/{dataset.pk}/pipelines/",
            {
                "name": "Invalid",
                "request_key": "invalid",
                "steps": [step],
            },
            format="json",
        )
        assert result.status_code == 400
    use.use(dataset, "eval", cell=dataset.active_cell)
    response = client.patch(
        f"/api/datasets/{dataset.pk}/", {"name": "Uncommitted", "intent": "train"}, format="json"
    )
    assert response.status_code == 400
    dataset.refresh_from_db()
    assert dataset.name == "Original"


def test_removed_chatgpt_routes_are_absent_and_local_accounts_remain():
    project, client, context = workspace()
    user_id = context.user.pk
    for path in (
        "",
        "start/",
        "login/",
        "callback/",
        "callback/session/",
        "disconnect/",
        "models/",
    ):
        assert client.get(f"/api/chatgpt/{path}").status_code == 404
        assert client.post(f"/api/chatgpt/{path}", {}, format="json").status_code == 404
    assert type(context.user).objects.filter(pk=user_id).exists()


def test_queued_source_cancellation_releases_the_dataset_without_publishing():
    from overbae.tasks import datasets as tasks

    project, client, _ = workspace()
    with patch.object(tasks.land, "apply_async"):
        created = client.post(
            "/api/datasets/",
            {
                "project": str(project.pk),
                "name": "Queued source",
                "intent": "explore",
                "source": {"rows": [{"text": "Evidence"}]},
            },
            format="json",
        )
    assert created.status_code == 201, created.data
    dataset = Dataset.objects.get(pk=created.data["id"])
    assert dataset.state == "landing"
    assert client.delete(f"/api/datasets/{dataset.pk}/").status_code == 409
    assert client.post(f"/api/datasets/{dataset.pk}/cancel/", {}, format="json").status_code == 202
    dataset.refresh_from_db()
    assert dataset.state == "idle"
    tasks.land(dataset_id=str(dataset.pk), source={"rows": [{"text": "Evidence"}]})
    dataset.refresh_from_db()
    assert dataset.state == "idle" and not dataset.cells.exists()
