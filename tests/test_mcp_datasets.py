from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from mcp_fixtures import call_tool

from overbae.models import (
    APIToken,
    Capability,
    Cell,
    Dataset,
    Project,
    ProjectMembership,
    Span,
    User,
)
from overbae.services.datasets import paths, store
from overbae.services.mcp import tools_datasets
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db


def _context(*, permission: str | list[str] = ("read", "write")) -> MCPContext:
    user = User.objects.create_user(
        email=f"mcp-dataset-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="Datasets", slug=f"datasets-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    permissions = [permission] if isinstance(permission, str) else list(permission)
    token = APIToken(
        scope={
            "scope": "project",
            "resourceIds": [str(project.id)],
            "permission": permissions,
        }
    )
    return MCPContext(user=user, token=token, project=project)


def _call(name: str, arguments: dict, context: MCPContext):
    return call_tool(name, arguments, context)


def _dataset(context: MCPContext, name: str = "Eval", **kwargs) -> Dataset:
    defaults = {
        "project": context.project,
        "name": name,
        "intent": Dataset.Intent.EVAL,
        "source_kind": Dataset.SourceKind.TRACES,
        "state": Dataset.State.IDLE,
    }
    defaults.update(kwargs)
    return Dataset.objects.create(**defaults)


def _ran_cell(dataset: Dataset, *, position: int = 0, title: str = "Source") -> Cell:
    return Cell.objects.create(
        dataset=dataset,
        position=position,
        title=title,
        state=Cell.State.OK,
        rows=3,
        fingerprint="f" * 64,
        columns=[{"name": "input", "type": "string"}],
        intent_report={"eval": {"ok": True, "reason": ""}},
        capability_report={"ok": True},
    )


def test_list_datasets_is_project_scoped_filtered_paginated_and_uses_uuids():
    context = _context()
    capability = Capability.objects.create(project=context.project, name="Support", slug="support")
    first = _dataset(context, "Alpha", capability=capability)
    _dataset(context, "Beta", intent=Dataset.Intent.TRAIN)
    other = Project.objects.create(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    Dataset.objects.create(project=other, name="Alpha foreign")

    result = _call(
        "list_datasets",
        {"capability": str(capability.id), "intent": "eval", "search": "Alpha", "limit": 1},
        context,
    )

    assert result.isError is False
    assert [item["id"] for item in result.structuredContent["datasets"]] == [str(first.id)]
    assert result.structuredContent["page"] == {
        "limit": 1,
        "offset": 0,
        "total": 1,
        "has_more": False,
        "next_cursor": None,
    }
    uuid.UUID(result.structuredContent["datasets"][0]["id"])


def test_inspect_is_bounded_ordered_and_refuses_an_ambiguous_name(monkeypatch):
    context = _context()
    dataset = _dataset(
        context,
        "Named dataset",
        capability_rank=[
            {"capability_id": str(uuid.uuid4()), "name": f"Capability {n}", "score": n}
            for n in range(25)
        ],
    )
    active = _ran_cell(dataset, position=0)
    dataset.active = active
    dataset.save(update_fields=["active"])
    Cell.objects.create(
        dataset=dataset,
        position=1,
        title="Proposal",
        state=Cell.State.FAILED,
        script="return df",
    )
    monkeypatch.setattr("pathlib.Path.exists", lambda _path: False)

    result = _call("inspect_dataset", {"dataset": str(dataset.id), "cell_limit": 20}, context)
    by_name = _call("inspect_dataset", {"dataset": dataset.name}, context)

    assert result.isError is False
    body = result.structuredContent
    assert [cell["position"] for cell in body["cells"]] == [0, 1]
    assert body["active"]["id"] == str(active.id)
    assert len(body["capability_rank"]) == 20
    assert "recent_chat" not in body
    assert body["next_actions"][0]["tool"] == "check_evaluation_readiness"
    assert body["next_actions"][0]["arguments"]["cell"] == str(active.id)
    assert by_name.isError is False
    assert by_name.structuredContent["id"] == str(dataset.id)
    Dataset.objects.create(project=context.project, name=dataset.name)
    twice = _call("inspect_dataset", {"dataset": dataset.name}, context)
    assert twice.structuredContent["error"]["code"] == "dataset_not_found"
    assert "use the dataset id" in twice.structuredContent["error"]["message"]


def test_query_is_project_and_cell_scoped_read_only_and_capped(monkeypatch):
    context = _context()
    dataset = _dataset(context)
    cell = _ran_cell(dataset)
    foreign_dataset = _dataset(context, "Foreign dataset")
    foreign_cell = _ran_cell(foreign_dataset)
    calls = []

    def query(sql, *, limit, **tables):
        calls.append((sql, limit, tables))
        return {"columns": ["input"], "rows": [{"input": str(n)} for n in range(limit)]}

    monkeypatch.setattr(tools_datasets.store, "query", query)
    ok = _call(
        "query_dataset",
        {"dataset": str(dataset.id), "cell": str(cell.id), "sql": "select input from t"},
        context,
    )
    foreign = _call(
        "query_dataset",
        {"dataset": str(dataset.id), "cell": str(foreign_cell.id), "sql": "select * from t"},
        context,
    )
    write = _call(
        "query_dataset",
        {"dataset": str(dataset.id), "sql": "delete from t"},
        context,
    )

    assert ok.isError is False
    assert len(ok.structuredContent["rows"]) == 100
    assert ok.structuredContent["columns"] == ["input"]
    assert ok.structuredContent["truncated"] is True
    assert calls[0][1] == 101
    assert foreign.structuredContent["error"]["code"] == "cell_not_found"
    assert write.structuredContent["error"]["code"] == "query_invalid"


def test_trace_creation_refuses_unknown_filters_mixed_sources_and_empty_selections():
    context = _context()
    unknown = _call(
        "create_dataset_from_traces",
        {"name": "Trace set", "filters": {"capability_name": "x"}},
        context,
    )
    assert unknown.isError is True
    assert unknown.structuredContent["error"]["code"] == "invalid_input"
    assert "capability_name" in unknown.structuredContent["error"]["message"]
    mixed = _call(
        "create_dataset_from_traces",
        {"name": "Trace set", "trace_ids": ["b" * 32], "filters": {"has_error": "true"}},
        context,
    )
    assert mixed.isError is True
    empty = _call(
        "create_dataset_from_traces", {"name": "Trace set", "trace_ids": ["b" * 32]}, context
    )
    assert empty.isError is True
    assert empty.structuredContent["error"]["code"] == "no_traces"
    assert Dataset.objects.filter(project=context.project).count() == 0


def test_list_and_inspect_map_legacy_ft_intent():
    context = _context()
    dataset = _dataset(context, name="Old train", intent="ft")

    listed = _call("list_datasets", {}, context)
    assert listed.isError is False
    row = next(
        item for item in listed.structuredContent["datasets"] if item["id"] == str(dataset.id)
    )
    assert row["intent"] == "train"

    filtered = _call("list_datasets", {"intent": "train"}, context)
    assert filtered.isError is False
    assert str(dataset.id) in {item["id"] for item in filtered.structuredContent["datasets"]}

    inspected = _call("inspect_dataset", {"dataset": str(dataset.id)}, context)
    assert inspected.isError is False
    assert inspected.structuredContent["intent"] == "train"


def _llm_span(project, capability, text="hello"):
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=uuid.uuid4().hex,
        project=project,
        capability=capability,
        span_type="llm_call",
        name="llm_call",
        start_time_ns=1,
        end_time_ns=2,
        duration_ns=1,
        status_code=0,
        attributes={
            "overmind.input.data": json.dumps([{"role": "user", "content": "question"}]),
            "overmind.output.data": json.dumps([{"role": "assistant", "content": text}]),
            "genai.model": "openai/gpt-5-mini",
        },
        usage={"genai.model": "openai/gpt-5-mini"},
    )


def test_llm_call_creation_refuses_an_empty_selection():
    context = _context()
    capability = Capability.objects.create(project=context.project, name="Support", slug="support")
    result = _call(
        "create_dataset_from_llm_calls",
        {
            "name": "Calls",
            "capability": str(capability.id),
            "since": "2099-01-01T00:00:00+00:00",
        },
        context,
    )
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "no_calls"
    assert Dataset.objects.filter(project=context.project).count() == 0


def test_llm_call_creation_lands_one_row_per_call():
    from django.utils import timezone

    context = _context()
    capability = Capability.objects.create(project=context.project, name="Support", slug="support")
    _llm_span(context.project, capability, text="recorded")
    result = _call(
        "create_dataset_from_llm_calls",
        {
            "name": "Calls",
            "capability": capability.slug,
            "since": (timezone.now() - timedelta(hours=1)).isoformat(),
            "intent": "eval",
        },
        context,
    )
    assert result.isError is False, result.structuredContent
    assert result.structuredContent["calls"] == 1
    dataset = Dataset.objects.get(pk=result.structuredContent["dataset"]["id"])
    assert dataset.state == Dataset.State.IDLE, dataset.error
    assert dataset.intent == Dataset.Intent.EVAL
    assert dataset.source_kind == Dataset.SourceKind.LLM_CALLS
    frame = store.read_frame(paths.cell_path(dataset.id, dataset.source.id))
    assert "trace_id" not in set(frame.columns)
    assert "span_id" in set(frame.columns)
    row = frame.iloc[0].to_dict()
    assert row["expected_output"]["content"] == "recorded"
    assert "trace_id" not in row
