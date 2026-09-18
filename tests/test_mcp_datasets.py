from __future__ import annotations

import asyncio
import json
import uuid

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
from overbae.services.mcp import tools_datasets
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db(transaction=True)


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


def test_dataset_catalog_has_only_six_bounded_tools():
    names = {definition.name for definition in CATALOG.definitions()}
    dataset_names = {
        name for name in names if "dataset" in name or name == "create_dataset_from_traces"
    }
    assert dataset_names == {
        "list_datasets",
        "inspect_dataset",
        "query_dataset",
        "create_dataset_from_traces",
        "message_dataset_agent",
        "run_dataset",
    }


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
        chat=[{"role": "user", "text": str(n), "at": f"t{n}"} for n in range(35)],
    )
    active = _ran_cell(dataset, position=0)
    dataset.active = active
    dataset.save(update_fields=["active"])
    Cell.objects.create(
        dataset=dataset,
        position=1,
        title="Proposal",
        state=Cell.State.PROPOSED,
        script="return df",
    )
    monkeypatch.setattr("pathlib.Path.exists", lambda _path: False)

    result = _call("inspect_dataset", {"dataset": str(dataset.id), "chat_limit": 30}, context)
    by_name = _call("inspect_dataset", {"dataset": dataset.name}, context)

    assert result.isError is False
    body = result.structuredContent
    assert [cell["position"] for cell in body["cells"]] == [0, 1]
    assert body["active"]["id"] == str(active.id)
    assert len(body["capability_rank"]) == 20
    assert len(body["recent_chat"]) == 30
    assert body["recent_chat"][0]["text"] == "5"
    assert body["next_actions"] == [
        {
            "tool": "run_dataset",
            "reason": "User must approve this proposed cell.",
            "arguments": {
                "dataset": str(dataset.id),
                "proposal_cell": str(dataset.cells.get(position=1).id),
            },
        }
    ]
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
        (Dataset.State.DIAGNOSING, "get_job"),
        (Dataset.State.RUNNING, "get_job"),
        (Dataset.State.ERROR, "message_dataset_agent"),
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


def test_message_agent_refuses_busy_then_queues_one_turn(monkeypatch):
    context = _context()
    dataset = _dataset(context, state=Dataset.State.LANDING)
    queued = []
    monkeypatch.setattr(
        "overbae.tasks.datasets.turn.apply_async",
        lambda **kwargs: queued.append(kwargs),
    )

    busy = _call(
        "message_dataset_agent",
        {"dataset": str(dataset.id), "message": "Shape this"},
        context,
    )
    Dataset.objects.filter(pk=dataset.pk).update(state=Dataset.State.IDLE)
    queued_result = _call(
        "message_dataset_agent",
        {"dataset": str(dataset.id), "message": "Shape this"},
        context,
    )

    dataset.refresh_from_db()
    assert busy.structuredContent["error"]["code"] == "dataset_busy"
    assert queued_result.isError is False
    assert dataset.state == Dataset.State.DIAGNOSING
    assert len(queued) == 1


def test_run_accepts_only_a_proposal_from_that_dataset(monkeypatch):
    context = _context()
    dataset = _dataset(context)
    _ran_cell(dataset)
    proposal = Cell.objects.create(
        dataset=dataset,
        position=1,
        title="Proposal",
        state=Cell.State.PROPOSED,
    )
    other = _dataset(context, "Other")
    foreign = Cell.objects.create(
        dataset=other,
        position=0,
        title="Foreign proposal",
        state=Cell.State.PROPOSED,
    )
    queued = []
    monkeypatch.setattr(
        "overbae.tasks.datasets.run.apply_async",
        lambda **kwargs: queued.append(kwargs),
    )

    rejected = _call(
        "run_dataset",
        {"dataset": str(dataset.id), "proposal_cell": str(foreign.id)},
        context,
    )
    accepted = _call(
        "run_dataset",
        {"dataset": str(dataset.id), "proposal_cell": str(proposal.id)},
        context,
    )

    assert rejected.structuredContent["error"]["code"] == "cell_not_found"
    assert accepted.isError is False
    assert accepted.structuredContent["dataset"]["state"] == Dataset.State.RUNNING
    assert len(queued) == 1


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
