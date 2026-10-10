from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta

import pytest

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
from overbae.services.datasets import land, paths, store
from overbae.services.mcp import tools_datasets
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db(transaction=True)


def test_start_dataset_preserves_brief_and_exposes_source_handoff():
    context = _context()
    result = _call(
        "start_dataset", {"brief": "Explore source documents before choosing a task."}, context
    )
    assert not result.isError, result.structuredContent
    dataset_id = result.structuredContent["dataset"]["id"]
    inspected = _call("inspect_dataset", {"dataset": dataset_id}, context)
    assert not inspected.isError, inspected.structuredContent
    detail = inspected.structuredContent
    assert detail["brief"] == "Explore source documents before choosing a task."
    assert detail["source_kind"] == "pending" and detail["capability"] is None
    assert detail["human_action"]["arguments"]["dataset"] == dataset_id


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
    return asyncio.run(CATALOG.call(name, arguments, context))


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


def test_inspection_exposes_the_workshops_source_families_and_consumer_requirements():
    context = _context()
    dataset = _dataset(context)
    land.land_rows(
        dataset,
        [
            {
                "input": {
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": "evidence"},
                    ]
                },
                "expected_output": {"answer": "supported"},
            }
            for prompt in ["Extract fields"] * 25 + ["Apply rules"] * 5
        ],
    )
    result = _call("inspect_dataset", {"dataset": str(dataset.id)}, context)
    assert not result.isError
    preparation = result.structuredContent["preparation_context"]
    assert preparation["profiles"]["source"]["rows_scanned"] == 30
    assert [family["rows"] for family in preparation["profiles"]["source"]["families"]] == [25, 5]
    assert "not the application" in preparation["consumers"]["model_evaluation"]["execution"]


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


@pytest.mark.parametrize(
    ("state", "tool"),
    [
        (Dataset.State.LANDING, "get_job"),
        (Dataset.State.RUNNING, "get_job"),
        (Dataset.State.ERROR, "inspect_dataset_workbench"),
    ],
)
def test_inspect_next_action_follows_dataset_state(state: str, tool: str):
    context = _context()
    dataset = _dataset(context, state=state, error="/private/tmp/secret.parquet")

    result = _call("inspect_dataset", {"dataset": str(dataset.id)}, context)

    assert result.structuredContent["next_actions"][0]["tool"] == tool
    assert "/private/tmp" not in json.dumps(result.structuredContent)


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


def _root_span(project, trace_id: str) -> Span:
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        project=project,
        span_type="entry_point",
        name="run",
        start_time_ns=1,
        end_time_ns=2,
        duration_ns=1,
        attributes={"overmind.input.data": trace_id, "overmind.output.data": "answer"},
    )


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


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("choice", ["automatic", "none", "selected"])
def test_trace_creation_respects_the_capability_choice(split, choice):
    context = _context()
    matched = Capability.objects.create(project=context.project, name="Matched", slug="matched")
    selected = Capability.objects.create(project=context.project, name="Selected", slug="selected")
    trace_ids = [uuid.uuid4().hex for _ in range(4)]
    for trace_id in trace_ids:
        span = _root_span(context.project, trace_id)
        span.capability = matched
        span.save(update_fields=["capability"])
    arguments = {"name": "Choice", "trace_ids": trace_ids}
    if choice != "automatic":
        arguments["capability"] = None if choice == "none" else str(selected.id)
    if split:
        arguments["split"] = {"eval_percent": 30, "position": "tail"}
    result = _call("create_dataset_from_traces", arguments, context)
    assert result.isError is False, result.structuredContent
    body = result.structuredContent
    ids = [body["dataset"]["id"]]
    if split:
        ids.append(body["eval_dataset"]["id"])
    expected = {"automatic": matched.id, "none": None, "selected": selected.id}[choice]
    for dataset in Dataset.objects.filter(pk__in=ids):
        assert dataset.state == Dataset.State.IDLE, dataset.error
        assert dataset.capability_id == expected
        assert dataset.capability_rank[0]["capability_id"] == str(matched.id)


def test_trace_creation_returns_a_dataset_run_receipt(monkeypatch):
    context = _context()
    Capability.objects.create(project=context.project, name="Support", slug="support")
    _root_span(context.project, "b" * 32)
    sources = []

    def create_dataset(**kwargs):
        sources.append(kwargs["source"])
        return _dataset(
            context,
            kwargs["name"],
            capability=kwargs.get("capability"),
            intent=kwargs.get("intent") or Dataset.Intent.PENDING,
            state=Dataset.State.LANDING,
        )

    monkeypatch.setattr(tools_datasets.dispatch, "create_dataset", create_dataset)

    traces = _call(
        "create_dataset_from_traces",
        {"name": "Trace set", "trace_ids": ["b" * 32]},
        context,
    )
    for result in (traces,):
        body = result.structuredContent
        assert body["job"]["kind"] == "dataset_run"
        assert body["job"]["id"] == body["dataset"]["id"]
        assert body["job"]["resource"]["uri"] == (
            f"overmind://jobs/dataset_run/{body['dataset']['id']}"
        )
        assert body["dataset"]["resource"]["uri"] in {
            link["uri"] for link in body["resource_links"]
        }
        assert body["traces"] == 1
    assert sources == [{"traces": {"trace_ids": ["b" * 32]}}]


def test_dataset_tool_text_is_complete_structured_json():
    context = _context()
    _dataset(context)

    result = _call("list_datasets", {}, context)

    assert json.loads(result.content[0].text) == result.structuredContent


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
