from __future__ import annotations

import asyncio
import uuid

import pytest

from overbae.models import (
    APIToken,
    Behaviour,
    BehaviourVersion,
    Capability,
    EvidenceProfile,
    Project,
    ProjectMembership,
    Span,
    TaskExecution,
    User,
)
from overbae.services.behaviour.instrumentation import instrumentation_tickets
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db(transaction=True)

SHA = "a" * 40


def _context(*, permission: str | list[str] = "read") -> MCPContext:
    user = User.objects.create_user(
        email=f"mcp-instrumentation-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(
        name="Instrumentation", slug=f"instrumentation-{uuid.uuid4().hex[:8]}"
    )
    ProjectMembership.objects.create(user=user, project=project)
    permissions = [permission] if isinstance(permission, str) else permission
    token = APIToken(
        scope={
            "scope": "project",
            "resourceIds": [str(project.id)],
            "permission": permissions,
        }
    )
    return MCPContext(user=user, token=token, project=project)


def _capability(context: MCPContext, name: str = "Support") -> Capability:
    return Capability.objects.create(
        project=context.project,
        name=name,
        slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}",
    )


def _behaviour(capability: Capability, key: str = "support-task") -> Behaviour:
    behaviour = Behaviour.objects.create(
        project=capability.project,
        capability=capability,
        key=key,
        display_name=key,
        entry_anchor="app.agent.run",
        grain=Behaviour.Grain.RUN,
        first_seen_sha=SHA,
        last_seen_sha=SHA,
    )
    BehaviourVersion.objects.create(
        behaviour=behaviour,
        analyzed_sha=SHA,
        contract={
            "entry_anchor": "app.agent.run",
            "anchor_sequence": ["app.agent.run", "app.agent.emit"],
            "anchors": [
                {"qualname": "app.agent.run", "kind": "entry_point", "file": "app/agent.py"},
                {"qualname": "app.agent.emit", "kind": "function", "file": "app/agent.py"},
            ],
        },
    )
    return behaviour


def _span(*, span_id: str | None = None, trace_id: str | None = None, **extra) -> dict:
    return {
        "span_id": span_id or uuid.uuid4().hex[:16],
        "trace_id": trace_id or uuid.uuid4().hex,
        "name": "run",
        "span_type": "entry_point",
        "start_time_ns": 1,
        "end_time_ns": 1_000_001,
        "attributes": extra,
        "resource_attrs": {"vcs.ref.head.revision": SHA},
        "status_code": 0,
    }


def _call(name: str, context: MCPContext, arguments: dict):
    return asyncio.run(CATALOG.call(name, arguments, context))


def test_catalog_has_exact_instrumentation_slice_and_read_annotations():
    assert len(CATALOG.definitions()) <= 36
    definitions = {definition.name: definition for definition in CATALOG.definitions()}
    assert {"get_instrumentation_plan", "verify_instrumentation"} <= set(definitions)
    assert all(
        definitions[name].read_only
        for name in ("get_instrumentation_plan", "verify_instrumentation")
    )
    assert {tool.name for tool in CATALOG.tools(frozenset({"read"}))} >= {
        "get_instrumentation_plan",
        "verify_instrumentation",
    }
    for name in ("get_instrumentation_plan", "verify_instrumentation"):
        tool = definitions[name].as_mcp_tool()
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False


def test_plan_preserves_exact_registered_ticket_fields():
    context = _context()
    capability = _capability(context)
    _behaviour(capability)

    expected = instrumentation_tickets(
        context.project,
        capability,
        "support-task",
    )["placements"]
    result = _call(
        "get_instrumentation_plan",
        context,
        {"capability": capability.slug, "behaviour": "support-task"},
    )

    assert result.isError is False
    placement = result.structuredContent["placements"][0]
    assert placement["key"] == expected[0]["key"]
    assert placement["behaviour_id"] == expected[0]["behaviour_id"]
    assert placement["version_id"] == expected[0]["version_id"]
    assert placement["version_analyzed_sha"] == expected[0]["version_analyzed_sha"]
    assert placement["contract_fingerprint"] == expected[0]["contract_fingerprint"]
    assert placement["capability_id"] == expected[0]["capability_id"]
    assert placement["target"]["file"] == expected[0]["target"]["file"]
    assert placement["target"]["qualname"] == expected[0]["target"]["qualname"]
    assert placement["required_scope"] == expected[0]["required_scope"]
    assert (
        placement["required_identity"]["capability_id"]
        == expected[0]["required_identity"]["capability_id"]
    )
    assert (
        placement["required_spans"][0]["required_decorator"]
        == expected[0]["required_spans"][0]["required_decorator"]
    )


def test_plan_requires_capability_for_behaviour_and_empty_registry_is_actionable():
    context = _context()

    invalid = _call("get_instrumentation_plan", context, {"behaviour": "support-task"})
    assert invalid.isError is True
    assert invalid.structuredContent["error"]["code"] == "invalid_input"

    empty = _call("get_instrumentation_plan", context, {})
    assert empty.isError is False
    assert empty.structuredContent["placements"] == []
    assert empty.structuredContent["human_action"]["code"] == "instrumentation_registry_empty"
    assert empty.structuredContent["instruction"]


def test_plan_and_verify_are_project_isolated():
    context = _context()
    other = _context()
    foreign = _capability(other, "Foreign")

    plan = _call("get_instrumentation_plan", context, {"capability": foreign.slug})
    verify = _call(
        "verify_instrumentation",
        context,
        {"capability": str(foreign.id), "spans": [_span()]},
    )

    assert plan.isError is True
    assert verify.isError is True
    assert plan.structuredContent["error"]["code"] == "capability_not_found"
    assert verify.structuredContent["error"]["code"] == "capability_not_found"


def test_verify_returns_typed_binding_grades_and_punch_list_without_writes():
    context = _context()
    capability = _capability(context)
    _behaviour(capability)
    spans = [_span(**{"overmind.behaviour.key": "support-task"})]
    counts = (
        Span.objects.count(),
        TaskExecution.objects.count(),
        EvidenceProfile.objects.count(),
    )

    result = _call(
        "verify_instrumentation",
        context,
        {"capability": capability.slug, "spans": spans},
    )

    assert result.isError is False
    output = result.structuredContent
    assert output["ok"] is True
    assert output["errors"] == []
    assert output["tasks"][0]["binding_source"] == "declared"
    assert output["tasks"][0]["capability_id"] == str(capability.id)
    assert set(output["capabilities"][0]["grades"]) == {
        "task",
        "units",
        "tool_ops",
        "provenance",
        "observations",
        "delivery",
    }
    assert "punch_list" in output["capabilities"][0]
    assert counts == (
        Span.objects.count(),
        TaskExecution.objects.count(),
        EvidenceProfile.objects.count(),
    )


def test_verify_reports_malformed_typed_span_and_rejects_json_string():
    context = _context()

    malformed = _call(
        "verify_instrumentation",
        context,
        {"spans": [{"trace_id": "missing-fields"}]},
    )
    encoded = _call(
        "verify_instrumentation",
        context,
        {"spans": "[]"},
    )

    assert malformed.isError is False
    assert malformed.structuredContent["ok"] is False
    assert malformed.structuredContent["errors"][0]["index"] == 0
    assert encoded.isError is True
    assert encoded.structuredContent["error"]["code"] == "invalid_input"
    assert "ValidationError" not in encoded.content[0].text


def test_verify_enforces_span_bound_and_redacts_unexpected_failures(monkeypatch):
    context = _context()
    too_many = _call(
        "verify_instrumentation",
        context,
        {"spans": [{} for _ in range(101)]},
    )
    assert too_many.isError is True
    assert too_many.structuredContent["error"]["code"] == "invalid_input"

    def fail(*_args, **_kwargs):
        raise RuntimeError("private provider detail")

    monkeypatch.setattr("overbae.services.mcp.tools_instrumentation.dry_run.verify_spans", fail)
    result = _call("verify_instrumentation", context, {"spans": []})
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "internal_error"
    assert "private provider detail" not in result.content[0].text
