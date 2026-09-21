from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from mcp import types
from starlette.testclient import TestClient

from overbae.models import (
    APIToken,
    Capability,
    Cell,
    Conversation,
    Dataset,
    DeployedModel,
    EvalRun,
    FinetuningJob,
    OptimizerExperiment,
    Project,
    ProjectMembership,
    Span,
    User,
)
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext, bind_context
from overbae.services.mcp.resources import (
    read_resource,
    resource_list,
    resource_templates,
    safe_json,
)
from overbae.services.mcp.server import create_mcp_application

pytestmark = pytest.mark.django_db(transaction=True)


def _project() -> tuple[Project, str]:
    user = User.objects.create_user(
        email=f"mcp-resource-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="Resources", slug=f"resources-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    raw_key, _ = APIToken.create_for_user(user, project=project)
    return project, raw_key


def _rpc(method: str, params: dict | None = None) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def test_resource_templates_cover_the_public_resource_surface():
    templates = {template.uriTemplate for template in resource_templates()}
    assert templates == {
        "overmind://capabilities/{capability}",
        "overmind://traces/{trace_id}",
        "overmind://sessions/{session}",
        "overmind://datasets/{dataset}",
        "overmind://eval-runs/{eval_run}",
        "overmind://eval-sets/{eval_set}",
        "overmind://finetunes/{job_id}",
        "overmind://deployments/{deployment}",
        "overmind://optimizer-runs/{experiment}",
        "overmind://jobs/{kind}/{id}",
        "overmind://connectors/{connector}",
    }


def test_dataset_upload_resource_describes_cli_flow_and_server_limits():
    project, _ = _project()
    token = APIToken(scope={"scope": "project", "permission": ["read"]})
    context = MCPContext(
        user=User(email="upload-resource@example.com"), token=token, project=project
    )

    async def read():
        with bind_context(context):
            contents = list(await read_resource("overmind://dataset-upload"))
        return json.loads(contents[0].content)

    resource = asyncio.run(read())
    assert resource["extensions"] == [".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".parquet"]
    assert resource["max_bytes"] == 2 * 1024**3
    assert resource["json_array_max_bytes"] == 256 * 1024**2
    assert "capped at 2 GiB" in resource["limits"]
    assert "capped at 256 MiB" in resource["limits"]
    assert resource["command"] == "overmind dataset upload FILE --json"
    assert "/inspect/" in resource["multiple_files"]
    assert "source.uploads" in resource["multiple_files"]
    assert "OVERMIND_API_KEY" in resource["auth"]
    assert "get_job" in resource["next_mcp_calls"][0]


def test_dataset_export_resource_describes_local_download_and_trace_flow():
    project, _ = _project()
    token = APIToken(scope={"scope": "project", "permission": ["read"]})
    context = MCPContext(
        user=User(email="export-resource@example.com"), token=token, project=project
    )

    async def read():
        with bind_context(context):
            contents = list(await read_resource("overmind://dataset-export"))
        return json.loads(contents[0].content)

    resource = asyncio.run(read())
    assert resource["command"] == "overmind dataset export DATASET --json"
    assert resource["formats"] == ["jsonl", "csv"]
    assert "dataset id" in resource["dataset"]
    assert "--api-url" in resource["config"]
    assert "--path" in resource["config"]
    assert (
        "select traces -> land a dataset from traces -> run dataset export locally"
        in resource["flow"]
    )
    assert "no export_trace MCP tool" in resource["trace_export"]


def test_checkpoint_download_resource_describes_cli_boundary_and_availability():
    project, _ = _project()
    token = APIToken(scope={"scope": "project", "permission": ["read"]})
    context = MCPContext(
        user=User(email="checkpoint-resource@example.com"), token=token, project=project
    )

    async def read():
        with bind_context(context):
            contents = list(await read_resource("overmind://checkpoint-download"))
        return json.loads(contents[0].content)

    resource = asyncio.run(read())
    assert resource["command"] == "overmind model download-checkpoint DEPLOYMENT --json"
    assert "deployment id supplied by MCP" in resource["deployment"]
    assert "OVERMIND_API_KEY" in resource["auth"]
    assert "--api-url" in resource["config"]
    assert "presigned S3" in resource["mcp_boundary"]
    assert "model context" in resource["mcp_boundary"]
    assert "baseten or modal" in resource["availability"]
    assert "archived checkpoints" in resource["availability"]
    assert "path" in resource["output"]
    assert "bytes_written" in resource["output"]
    assert "refuses to overwrite" in resource["overwrite"]


def test_connector_setup_resource_describes_human_cli_boundary():
    project, _ = _project()
    token = APIToken(scope={"scope": "project", "permission": ["read"]})
    context = MCPContext(
        user=User(email="connector-setup@example.com"), token=token, project=project
    )

    async def read():
        with bind_context(context):
            contents = list(await read_resource("overmind://connector-setup"))
        return json.loads(contents[0].content)

    resource = asyncio.run(read())
    encoded = json.dumps(resource)
    assert resource["command"] == "overmind connector add langfuse --json"
    assert resource["types"][0]["env"] == ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]
    assert "Do not have the agent export keys" in resource["boundary"]
    assert "inspect_connectors" in resource["next_mcp_calls"][0]
    assert "needs_secret" not in encoded
    assert "api_key" not in encoded


def test_static_resource_manifest_includes_checkpoint_download_guidance():
    resources = resource_list()

    assert len(resources) == 5
    uris = {str(resource.uri) for resource in resources}
    assert "overmind://checkpoint-download" in uris
    assert "overmind://connector-setup" in uris


def test_resource_reads_are_json_and_project_scoped():
    project, _ = _project()
    capability = Capability.objects.create(project=project, name="Support", slug="support")
    session = Conversation.objects.create(project=project, external_id="session-1", name="Session")
    Span.objects.create(
        span_id="resource-root",
        trace_id="a" * 32,
        project=project,
        capability=capability,
        conversation=session,
        name="request",
        start_time_ns=1,
        end_time_ns=2,
        duration_ns=1,
        status_code=1,
    )
    dataset = Dataset.objects.create(project=project, name="Eval")
    eval_run = EvalRun.objects.create(project=project, name="Run", dataset=dataset)
    finetune = FinetuningJob.objects.create(
        project=project,
        capability=capability,
        dataset=dataset,
        base_model="model/base",
    )
    deployment = DeployedModel.objects.create(project=project, model_id="ft-support")
    optimizer = OptimizerExperiment.objects.create(
        project=project,
        capability=capability,
        dataset=dataset,
    )
    token = APIToken(scope={"scope": "project", "permission": ["read"]})
    context = MCPContext(user=User(email="resource@example.com"), token=token, project=project)

    uris = [
        "overmind://project/current",
        f"overmind://capabilities/{capability.id}",
        f"overmind://traces/{'a' * 32}",
        f"overmind://sessions/{session.id}",
        f"overmind://datasets/{dataset.id}",
        f"overmind://eval-runs/{eval_run.id}",
        f"overmind://finetunes/{finetune.id}",
        f"overmind://deployments/{deployment.id}",
        f"overmind://optimizer-runs/{optimizer.id}",
        f"overmind://jobs/dataset_run/{dataset.id}",
    ]

    async def read_all():
        with bind_context(context):
            for uri in uris:
                contents = list(await read_resource(uri))
                assert contents[0].mime_type == "application/json"
                assert '"uri":' in contents[0].content

    asyncio.run(read_all())


@pytest.mark.parametrize(("span_count", "truncated"), [(100, False), (101, True)])
def test_trace_resource_uses_the_verifier_span_limit(span_count, truncated):
    project, _ = _project()
    trace_id = f"{span_count:032d}"
    root_id = f"{span_count:03d}{0:013d}"
    Span.objects.bulk_create(
        [
            Span(
                span_id=f"{span_count:03d}{index:013d}",
                trace_id=trace_id,
                parent_span_id=None if index == 0 else root_id,
                project=project,
                name="run" if index == 0 else "step",
                start_time_ns=index,
                end_time_ns=index + 1,
                duration_ns=1,
            )
            for index in range(span_count)
        ]
    )

    resource = _read(project, f"overmind://traces/{trace_id}")

    assert resource["span_count"] == span_count
    assert resource["truncated"] is truncated
    assert len(resource["spans"]) == min(span_count, 100)


def test_trace_resource_preserves_resource_attrs_for_verification():
    project, _ = _project()
    trace_id = "3" * 32
    resource_attrs = {"overmind.capability.id": str(uuid.uuid4())}
    Span.objects.create(
        span_id="resource-attrs-root",
        trace_id=trace_id,
        project=project,
        name="run",
        start_time_ns=1,
        end_time_ns=2,
        duration_ns=1,
        resource_attrs=resource_attrs,
    )

    resource = _read(project, f"overmind://traces/{trace_id}")

    assert resource["spans"][0]["resource_attrs"] == resource_attrs


def test_safe_json_redacts_nested_secret_key_styles():
    secret_values = {
        "api-secret",
        "refresh-secret",
        "private-secret",
    }
    redacted = safe_json(
        {
            "apiKey": "api-secret",
            "REFRESH.TOKEN": "refresh-secret",
            "nested": [{"private-key": "private-secret", "label": "kept"}],
        }
    )

    encoded = json.dumps(redacted)
    for secret in secret_values:
        assert secret not in encoded
    assert redacted == {"nested": [{"label": "kept"}]}


def test_finetune_resource_redacts_all_checkpoint_url_styles():
    project, _ = _project()
    signed_url = "https://storage.example/checkpoint?X-Amz-Signature=signed-value"
    dataset = Dataset.objects.create(project=project, name="Training")
    job = FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model="model/base",
        progress={
            "checkpoint_uri": signed_url,
            "checkpointUri": signed_url,
            "CHECKPOINT.URI": signed_url,
            "download_url": signed_url,
            "downloadUrl": signed_url,
            "DOWNLOAD-URL": signed_url,
            "nested": {
                "apiKey": "api-secret",
                "refreshToken": "refresh-secret",
                "safe": "kept",
            },
        },
    )
    token = APIToken(scope={"scope": "project", "permission": ["read"]})
    context = MCPContext(user=User(email="resource@example.com"), token=token, project=project)

    async def read():
        with bind_context(context):
            contents = list(await read_resource(f"overmind://finetunes/{job.id}"))
        return json.loads(contents[0].content)

    resource = asyncio.run(read())
    encoded = json.dumps(resource)
    assert signed_url not in encoded
    assert "api-secret" not in encoded
    assert "refresh-secret" not in encoded
    assert resource["progress"] == {"nested": {"safe": "kept"}}


def test_resource_read_through_transport_returns_safe_not_found_error():
    project, raw_key = _project()
    del project
    with TestClient(create_mcp_application()) as client:
        response = client.post(
            "/api/mcp/",
            json=_rpc("resources/read", {"uri": "overmind://traces/missing"}),
            headers={"X-Api-Key": raw_key, "Accept": "application/json"},
        )

    assert response.status_code == 200
    error = response.json()["error"]
    assert error["code"] == 404
    assert "missing" in error["message"]
    assert "Traceback" not in response.text


def _read(project, uri: str) -> dict:
    token = APIToken(scope={"scope": "project", "permission": ["read"]})
    context = MCPContext(user=User(email="resource@example.com"), token=token, project=project)

    async def read():
        with bind_context(context):
            contents = list(await read_resource(uri))
        return json.loads(contents[0].content)

    return asyncio.run(read())


def _tool_context(project) -> MCPContext:
    token = APIToken(scope={"scope": "project", "permission": ["read"]})
    return MCPContext(user=User(email="job@example.com"), token=token, project=project)


def test_dataset_resource_matches_inspect_fields_and_hides_paths(settings):
    from conftest import EVAL_ROWS

    from overbae.services.datasets import land

    project, _ = _project()
    capability = Capability.objects.create(project=project, name="Support", slug="support")
    dataset = Dataset.objects.create(
        project=project,
        name="Eval set",
        capability=capability,
        intent=Dataset.Intent.EVAL,
        state=Dataset.State.LANDING,
        chat=[
            {"role": "user", "text": "shape this", "at": "2026-09-09T00:00:00+00:00"},
            {
                "role": "agent",
                "text": "done",
                "error": "",
                "cells": [{"id": "c1"}],
                "ms": 12,
                "at": "2026-09-09T00:00:01+00:00",
            },
        ],
    )
    land.land_rows(dataset, list(EVAL_ROWS))
    dataset.refresh_from_db()
    from overbae.services.mcp.contracts.datasets import serialize_dataset_detail

    payload = _read(project, f"overmind://datasets/{dataset.id}")
    encoded = json.dumps(payload)
    local = serialize_dataset_detail(dataset).model_dump(mode="json", by_alias=True)

    assert payload["id"] == str(dataset.id)
    assert payload["name"] == dataset.name
    assert payload["intent"] == Dataset.Intent.EVAL
    assert payload["source_kind"] == Dataset.SourceKind.FILE
    assert payload["state"] == Dataset.State.IDLE
    assert payload["capability"]["id"] == str(capability.id)
    assert payload["active"]["rows"] == 2
    assert payload["active"]["id"]
    assert payload["active"]["version"]
    assert payload["cells"]
    assert payload["cells_truncated"] is False
    assert payload["sample"]["cell_id"] == payload["active"]["id"]
    assert len(payload["sample"]["rows"]) == 2
    assert {turn["role"] for turn in payload["recent_chat"]} >= {"user", "agent"}
    assert payload["next_actions"]
    assert payload["human_action"]["command"] == "overmind dataset export DATASET --json"
    assert "api_key" not in json.dumps(payload["human_action"]).lower()
    assert "OVERMIND_API_KEY" not in encoded
    assert str(settings.MEDIA_ROOT) not in encoded
    assert ".parquet" not in encoded
    assert "upload/" not in encoded
    assert local["id"] == payload["id"]
    assert local["cells"]
    assert local["human_action"]["command"] == payload["human_action"]["command"]


def test_get_job_dataset_run_tracks_state_and_emits_job_resource_link():
    project, _ = _project()
    dataset = Dataset.objects.create(
        project=project,
        name="Notebook",
        state=Dataset.State.LANDING,
        chat=[{"role": "user", "text": "land it", "at": "t0"}],
    )
    Cell.objects.create(dataset=dataset, position=0, title="Source", state=Cell.State.QUEUED)
    context = _tool_context(project)
    uri = f"overmind://jobs/dataset_run/{dataset.id}"

    states = (
        Dataset.State.LANDING,
        Dataset.State.DIAGNOSING,
        Dataset.State.RUNNING,
        Dataset.State.IDLE,
        Dataset.State.ERROR,
    )
    for state in states:
        Dataset.objects.filter(pk=dataset.pk).update(
            state=state, error="clipped-secret" if state == Dataset.State.ERROR else ""
        )
        result = asyncio.run(
            CATALOG.call("get_job", {"kind": "dataset_run", "id": str(dataset.id)}, context)
        )
        assert result.isError is False, result.structuredContent
        body = result.structuredContent
        assert body["kind"] == "dataset_run"
        assert body["id"] == str(dataset.id)
        assert body["status"] == state
        assert body["resource"]["uri"] == uri
        assert body["details"]["next_action"]["tool"]
        links = [str(item.uri) for item in result.content if isinstance(item, types.ResourceLink)]
        assert uri in links
        assert f"overmind://datasets/{dataset.id}" in links
        job = _read(project, uri)
        assert job["status"] == state
        assert job["dataset"]["uri"] == f"overmind://datasets/{dataset.id}"
        assert job["latest_turn"]["role"] == "user"

    assert result.structuredContent["job_error"] == "clipped-secret"
    assert result.structuredContent["details"]["next_action"]["tool"] in {
        "inspect_dataset",
        "message_dataset_agent",
    }


def test_dataset_run_job_is_project_scoped():
    project, _ = _project()
    other = Project.objects.create(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    foreign = Dataset.objects.create(project=other, name="Foreign")
    context = _tool_context(project)

    missing = asyncio.run(
        CATALOG.call("get_job", {"kind": "dataset_run", "id": str(foreign.id)}, context)
    )
    retired = asyncio.run(
        CATALOG.call("get_job", {"kind": "dataset_build", "id": str(foreign.id)}, context)
    )

    assert missing.isError is True
    assert missing.structuredContent["error"]["code"] == "resource_not_found"
    assert retired.isError is True
    assert retired.structuredContent["error"]["code"] == "invalid_input"

    from mcp.shared.exceptions import McpError

    with pytest.raises(McpError):
        _read(project, f"overmind://datasets/{foreign.id}")
    with pytest.raises(McpError):
        _read(project, f"overmind://jobs/dataset_run/{foreign.id}")
