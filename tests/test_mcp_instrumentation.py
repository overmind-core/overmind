from __future__ import annotations

import asyncio
import uuid

import pytest
from factories import make_matrix_row
from mcp_fixtures import mcp_context

from overbae.models import Capability, EvalScore, Span
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db(transaction=True)

TOOLS = ("get_instrumentation_plan", "verify_instrumentation")


def _capability(context: MCPContext, name: str = "Support", anchors=None) -> Capability:
    card = {"anchors": anchors} if anchors is not None else {}
    return Capability.objects.create(
        project=context.project,
        name=name,
        slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}",
        improvement_metadata={"capability_card": card},
    )


def _span(
    *, span_id=None, trace_id=None, parent_span_id=None, span_type="entry_point", **attrs
) -> dict:
    return {
        "span_id": span_id or uuid.uuid4().hex[:16],
        "trace_id": trace_id or uuid.uuid4().hex,
        "parent_span_id": parent_span_id,
        "name": "run",
        "span_type": span_type,
        "start_time_ns": 1,
        "end_time_ns": 1_000_001,
        "attributes": attrs,
        "status_code": 0,
    }


def _call(name: str, context: MCPContext, arguments: dict):
    return asyncio.run(CATALOG.call(name, arguments, context))


def test_catalog_exposes_both_tools_as_read_only():
    definitions = {definition.name: definition for definition in CATALOG.definitions()}
    assert {tool.name for tool in CATALOG.tools(frozenset({"read"}))} >= set(TOOLS)
    for name in TOOLS:
        tool = definitions[name].as_mcp_tool()
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.openWorldHint is False


def test_plan_places_decorators_from_card_anchors_entry_point_first():
    context = mcp_context()
    capability = _capability(
        context,
        anchors=[
            {"qualname": "support.tools.lookup", "kind": "tool", "file": "support/tools.py"},
            {
                "qualname": "support.agent.Agent.run",
                "kind": "entry_point",
                "file": "support/agent.py#L4",
            },
            {"qualname": "support.misc.helper", "kind": "unknown", "file": "support/misc.py"},
        ],
    )

    placements = _call("get_instrumentation_plan", context, {"capability": capability.slug})

    assert placements.isError is False
    rows = placements.structuredContent["placements"]
    assert [row["kind"] for row in rows] == ["entry_point", "function", "tool"]
    assert rows[0]["required_decorator"] == (
        f'@overmind.entry_point(capability_id="{capability.id}")'
    )
    assert rows[0]["target"] == {
        "file": "support/agent.py",
        "qualname": "support.agent.Agent.run",
        "module": "support.agent",
        "import_line": "from support.agent import Agent",
    }


def test_plan_is_project_wide_without_a_capability():
    context = mcp_context()
    for name in ("Billing", "Support"):
        _capability(context, name, anchors=[{"qualname": f"{name}.run", "kind": "entry_point"}])

    result = _call("get_instrumentation_plan", context, {})

    assert [row["capability"] for row in result.structuredContent["placements"]] == [
        "Billing",
        "Support",
    ]


def test_plan_without_anchors_asks_for_setup():
    context = mcp_context()
    _capability(context)

    output = _call("get_instrumentation_plan", context, {}).structuredContent

    assert output["placements"] == []
    assert output["human_action"]["code"] == "instrumentation_registry_empty"


def test_plan_and_verify_are_project_isolated():
    context = mcp_context()
    foreign = _capability(mcp_context(), "Foreign")

    plan = _call("get_instrumentation_plan", context, {"capability": foreign.slug})
    verify = _call(
        "verify_instrumentation", context, {"capability": str(foreign.id), "spans": [_span()]}
    )

    assert plan.structuredContent["error"]["code"] == "capability_not_found"
    assert verify.structuredContent["error"]["code"] == "capability_not_found"


def test_verify_passes_a_complete_run_without_writes():
    context = mcp_context()
    capability = _capability(context)
    make_matrix_row(context.project, "Answers", capability=capability)
    root = _span(**{"overmind.capability.id": str(capability.id), "input": "hi", "output": "hello"})
    counts = (Span.objects.count(), EvalScore.objects.count())

    output = _call("verify_instrumentation", context, {"spans": [root]}).structuredContent

    assert output["ok"] is True
    [result] = output["capabilities"]
    assert result["capability_id"] == str(capability.id)
    assert set(result["checks"].values()) == {"ok"}
    assert result["spans"][0]["rows"] == ["Answers"]
    assert counts == (Span.objects.count(), EvalScore.objects.count())


def test_verify_names_each_missing_piece():
    context = mcp_context()
    capability = _capability(context)
    Capability.objects.filter(pk=capability.pk).update(
        improvement_metadata={"capability_card": {"tool_spec": [{"name": "lookup"}]}}
    )
    trace_id = uuid.uuid4().hex
    spans = [_span(trace_id=trace_id), _span(trace_id=trace_id)]

    output = _call(
        "verify_instrumentation", context, {"capability": capability.slug, "spans": spans}
    ).structuredContent

    assert output["ok"] is False
    [result] = output["capabilities"]
    assert {item["check"] for item in result["punch_list"]} == {
        "root",
        "input",
        "output",
        "tools",
        "scored_rows",
    }


def test_verify_treats_an_empty_captured_payload_as_missing():
    context = mcp_context()
    capability = _capability(context)
    make_matrix_row(context.project, "Answers", capability=capability)
    root = _span(
        **{"overmind.capability.id": str(capability.id), "inputs": "{}", "outputs": '"hello"'}
    )

    output = _call("verify_instrumentation", context, {"spans": [root]}).structuredContent

    [result] = output["capabilities"]
    assert result["checks"]["input"] == "missing"
    assert result["checks"]["output"] == "ok"


def test_verify_reports_malformed_spans_and_rejects_a_json_string():
    context = mcp_context()

    malformed = _call("verify_instrumentation", context, {"spans": [{"trace_id": "x"}]})
    encoded = _call("verify_instrumentation", context, {"spans": "[]"})

    assert malformed.structuredContent["ok"] is False
    assert malformed.structuredContent["errors"][0]["index"] == 0
    assert encoded.structuredContent["error"]["code"] == "invalid_input"


def test_verify_bounds_caller_spans():
    context = mcp_context()

    at_limit = _call("verify_instrumentation", context, {"spans": [_span() for _ in range(100)]})
    too_many = _call("verify_instrumentation", context, {"spans": [{} for _ in range(101)]})

    assert at_limit.isError is False
    assert too_many.structuredContent["error"]["code"] == "invalid_input"


def test_verify_grades_an_ingested_trace_beyond_the_caller_bound():
    context = mcp_context()
    capability = _capability(context)
    make_matrix_row(context.project, "Answers", capability=capability)
    trace_id = uuid.uuid4().hex
    root = _span(
        trace_id=trace_id,
        **{"overmind.capability.id": str(capability.id), "input": "hi", "output": "hello"},
    )
    rows = [root] + [
        _span(trace_id=trace_id, parent_span_id=root["span_id"], span_type="function")
        for _ in range(150)
    ]
    Span.objects.bulk_create(
        [Span(project=context.project, duration_ns=1_000_000, **row) for row in rows]
    )

    result = _call("verify_instrumentation", context, {"trace_id": trace_id})
    foreign = _call("verify_instrumentation", mcp_context(), {"trace_id": trace_id})
    both = _call("verify_instrumentation", context, {"trace_id": trace_id, "spans": [_span()]})

    assert result.structuredContent["ok"] is True
    assert foreign.structuredContent["error"]["code"] == "resource_not_found"
    assert both.structuredContent["error"]["code"] == "invalid_input"
