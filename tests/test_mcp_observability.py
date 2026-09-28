from __future__ import annotations

import asyncio
import uuid

import pytest

from overbae.models import (
    APIToken,
    Capability,
    Conversation,
    Dataset,
    EvalRun,
    FinetuningJob,
    Project,
    ProjectMembership,
    Span,
    TaskExecution,
    User,
)
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db(transaction=True)


def _context() -> tuple[MCPContext, Capability, Dataset]:
    user = User.objects.create_user(
        email=f"mcp-tool-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="Tools", slug=f"tools-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    token = APIToken(scope={"scope": "project", "permission": ["read"]})
    capability = Capability.objects.create(project=project, name="Support", slug="support")
    dataset = Dataset.objects.create(project=project, name="Eval", capability=capability)
    return MCPContext(user=user, token=token, project=project), capability, dataset


def test_all_tools_return_structured_content_and_project_scoped_rows():
    context, capability, dataset = _context()
    Span.objects.create(
        span_id="tool-root",
        trace_id="b" * 32,
        project=context.project,
        capability=capability,
        name="support request",
        start_time_ns=1,
        end_time_ns=2,
        duration_ns=1,
        status_code=1,
    )
    TaskExecution.objects.create(
        project=context.project,
        capability=capability,
        trace_id="b" * 32,
        unit_span_id="tool-root",
        status=TaskExecution.Status.COMPLETED,
    )
    eval_run = EvalRun.objects.create(project=context.project, name="Eval", dataset=dataset)
    finetune = FinetuningJob.objects.create(
        project=context.project,
        capability=capability,
        dataset=dataset,
        base_model="model/base",
    )

    calls = [
        ("inspect_capability_health", {"capability": "support", "days": 7}),
        ("query_failures", {"capability": "support"}),
        ("query_traces", {"capability": "support"}),
        ("query_task_executions", {"capability": "support"}),
        ("get_job", {"kind": "eval_run", "id": str(eval_run.id)}),
        ("get_job", {"kind": "finetune", "id": str(finetune.id)}),
        ("get_job", {"kind": "dataset_run", "id": str(dataset.id)}),
    ]

    async def invoke():
        results = []
        for name, args in calls:
            results.append(await CATALOG.call(name, args, context))
        return results

    results = asyncio.run(invoke())
    for (name, _), result in zip(calls, results, strict=True):
        assert result.isError is False, (name, result.content, result.structuredContent)
    assert all(result.structuredContent for result in results)
    # Keyed by call, not index, so adding or dropping a tool cannot silently reindex.
    body = {
        name: result.structuredContent for (name, _), result in zip(calls, results, strict=True)
    }
    eval_job, finetune_job, dataset_job = (r.structuredContent for r in results[-3:])

    assert body["query_traces"]["traces"][0]["trace_id"] == "b" * 32
    assert body["query_task_executions"]["task_executions"][0]["trace_id"] == "b" * 32
    assert eval_job["resource"]["uri"] == f"overmind://eval-runs/{eval_run.id}"
    assert finetune_job["resource"]["uri"] == f"overmind://finetunes/{finetune.id}"
    assert dataset_job["resource"]["uri"] == f"overmind://jobs/dataset_run/{dataset.id}"
    assert dataset_job["kind"] == "dataset_run"
    assert dataset_job["id"] == str(dataset.id)


def test_invalid_tool_input_is_safe_and_typed():
    context, _, _ = _context()

    async def invoke():
        return await CATALOG.call("query_traces", {"unexpected": True}, context)

    result = asyncio.run(invoke())
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "invalid_input"
    assert "ValidationError" not in result.content[0].text


def test_query_traces_exact_session_counts_root_traces():
    context, capability, _ = _context()
    correlation = "instrumentation-smoke-test"
    session = Conversation.objects.create(
        project=context.project,
        capability=capability,
        external_id=correlation,
    )
    first_trace = "1" * 32
    second_trace = "2" * 32
    Span.objects.bulk_create(
        [
            Span(
                span_id="session-root-one",
                trace_id=first_trace,
                project=context.project,
                capability=capability,
                conversation=session,
                name="run",
                start_time_ns=1,
            ),
            Span(
                span_id="session-child-1",
                trace_id=first_trace,
                parent_span_id="session-root-one",
                project=context.project,
                capability=capability,
                conversation=session,
                name="step",
                start_time_ns=2,
            ),
            Span(
                span_id="session-root-two",
                trace_id=second_trace,
                project=context.project,
                capability=capability,
                conversation=session,
                name="run",
                start_time_ns=3,
            ),
        ]
    )

    async def invoke():
        return await CATALOG.call(
            "query_traces",
            {"session": correlation, "all_spans": False, "limit": 2},
            context,
        )

    result = asyncio.run(invoke())

    assert result.isError is False
    assert result.structuredContent["page"]["total"] == 2
    assert {row["trace_id"] for row in result.structuredContent["traces"]} == {
        first_trace,
        second_trace,
    }
