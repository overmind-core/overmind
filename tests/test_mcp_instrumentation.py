from __future__ import annotations

import uuid

import pytest
from factories import make_matrix_row
from mcp_fixtures import call_tool, mcp_context

from overbae.models import Capability, Span
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db

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
    return call_tool(name, arguments, context)


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


def test_verify_reports_malformed_spans_and_rejects_a_json_string():
    context = mcp_context()

    malformed = _call("verify_instrumentation", context, {"spans": [{"trace_id": "x"}]})
    encoded = _call("verify_instrumentation", context, {"spans": "[]"})

    assert malformed.structuredContent["ok"] is False
    assert malformed.structuredContent["errors"][0]["index"] == 0
    assert encoded.structuredContent["error"]["code"] == "invalid_input"


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
