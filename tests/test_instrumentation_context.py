from __future__ import annotations

import asyncio
import uuid

import pytest

from overbae.models import (
    APIToken,
    Behaviour,
    BehaviourVersion,
    Capability,
    Project,
    ProjectMembership,
    User,
)
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db(transaction=True)

SHA = "a" * 40


def _context() -> MCPContext:
    user = User.objects.create_user(
        email=f"plan-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    token = APIToken(
        scope={
            "scope": "project",
            "resourceIds": [str(project.id)],
            "permission": ["read"],
        }
    )
    return MCPContext(user=user, token=token, project=project)


def _capability(project: Project, name: str = "Agent") -> Capability:
    return Capability.objects.create(
        project=project,
        name=name,
        slug=f"{name.lower()}-{uuid.uuid4().hex[:6]}",
    )


def _behaviour(
    capability: Capability,
    key: str,
    *,
    entry: str,
    file: str,
    grain: str = Behaviour.Grain.RUN,
    anchors: list[dict[str, object]] | None = None,
    anchor_sequence: list[str] | None = None,
) -> tuple[Behaviour, BehaviourVersion]:
    behaviour = Behaviour.objects.create(
        project=capability.project,
        capability=capability,
        key=key,
        display_name=key,
        entry_anchor=entry,
        grain=grain,
        first_seen_sha=SHA,
        last_seen_sha=SHA,
    )
    version = BehaviourVersion.objects.create(
        behaviour=behaviour,
        analyzed_sha=SHA,
        contract={
            "entry_anchor": entry,
            "anchors": anchors
            if anchors is not None
            else [
                {
                    "qualname": entry,
                    "kind": "entry",
                    "file": file,
                }
            ],
            "anchor_sequence": anchor_sequence if anchor_sequence is not None else [entry],
        },
    )
    return behaviour, version


def _call(name: str, context: MCPContext, arguments: dict):
    return asyncio.run(CATALOG.call(name, arguments, context))


def _placements(context: MCPContext, arguments: dict) -> list[dict]:
    result = _call("get_instrumentation_plan", context, arguments)
    assert result.isError is False
    return result.structuredContent["placements"]


def test_plan_uses_registry_contract_target_and_only_stable_ticket_fields():
    context = _context()
    capability = _capability(context.project)
    behaviour, version = _behaviour(
        capability,
        "run_agent",
        entry="app.main.run_agent",
        file="app/main.py",
    )
    ticket = _placements(context, {"capability": capability.slug, "behaviour": behaviour.key})[0]

    assert ticket["target"] == {
        "file": "app/main.py",
        "qualname": "app.main.run_agent",
        "module": "app.main",
        "import_line": "from app.main import run_agent",
    }
    assert ticket["behaviour_id"] == str(behaviour.id)
    assert ticket["version_id"] == str(version.id)
    assert ticket["version_analyzed_sha"] == SHA
    assert ticket["required_scope"] == f'@overmind.run(capability_id="{capability.id}")'
    assert ticket["required_identity"]["capability_id"] == str(capability.id)
    assert "smoke_hint" not in ticket
    assert "contract_snapshot" not in ticket
    assert set(ticket) == {
        "key",
        "behaviour_id",
        "version_id",
        "version_analyzed_sha",
        "contract_fingerprint",
        "capability",
        "capability_id",
        "placement_mode",
        "allowed_keys",
        "grain",
        "target",
        "required_scope",
        "required_spans",
        "required_identity",
    }
    assert ticket["required_spans"] == []


def test_plan_filters_by_behaviour_key_or_id_and_excludes_retired():
    context = _context()
    capability = _capability(context.project)
    first, _ = _behaviour(capability, "first", entry="app.first.run", file="app/first.py")
    second, _ = _behaviour(capability, "second", entry="app.second.run", file="app/second.py")
    retired = Behaviour.objects.create(
        project=context.project,
        capability=capability,
        key="retired",
        display_name="retired",
        entry_anchor="app.old.run",
        grain=Behaviour.Grain.RUN,
        status=Behaviour.Status.RETIRED,
    )
    BehaviourVersion.objects.create(
        behaviour=retired,
        analyzed_sha=SHA,
        contract={"entry_anchor": retired.entry_anchor},
    )

    all_tickets = _placements(context, {"capability": capability.slug})
    key_tickets = _placements(context, {"capability": capability.slug, "behaviour": "FIRST"})
    id_tickets = _placements(context, {"capability": capability.slug, "behaviour": str(second.id)})

    assert {ticket["key"] for ticket in all_tickets} == {"first", "second"}
    assert [ticket["key"] for ticket in key_tickets] == [first.key]
    assert [ticket["key"] for ticket in id_tickets] == [second.key]
    assert "retired" not in {ticket["key"] for ticket in all_tickets}


def test_plan_uses_task_scope_for_fixed_turn():
    context = _context()
    capability = _capability(context.project)
    anchors = [
        {
            "qualname": "app.main.turn_agent",
            "kind": "entry_point",
            "file": "app/main.py",
        },
        {
            "qualname": "app.main.lookup",
            "kind": "retrieval",
            "file": "app/main.py",
        },
    ]
    behaviour, _ = _behaviour(
        capability,
        "turn_agent",
        entry="app.main.turn_agent",
        file="app/main.py",
        grain=Behaviour.Grain.TURN,
        anchors=anchors,
        anchor_sequence=["app.main.turn_agent", "app.main.lookup"],
    )

    ticket = _placements(context, {"capability": capability.slug, "behaviour": behaviour.key})[0]

    assert ticket["required_scope"] == '@overmind.task("turn_agent", unit="turn")'
    assert [
        (span["target"]["qualname"], span["required_decorator"])
        for span in ticket["required_spans"]
    ] == [
        ("app.main.turn_agent", "@overmind.observe()"),
        ("app.main.lookup", "@overmind.retrieval()"),
    ]


def test_plan_marks_shared_entry_dynamic():
    context = _context()
    capability = _capability(context.project)
    anchors = [
        {
            "qualname": "app.dispatch.run",
            "kind": "entry_point",
            "file": "app/dispatch.py",
        }
    ]
    _behaviour(
        capability,
        "first",
        entry="app.dispatch.run",
        file="app/dispatch.py",
        anchors=anchors,
        anchor_sequence=["app.dispatch.run"],
    )
    _behaviour(
        capability,
        "second",
        entry="app.dispatch.run",
        file="app/dispatch.py",
        anchors=anchors,
        anchor_sequence=["app.dispatch.run"],
    )

    result = _call("get_instrumentation_plan", context, {"capability": capability.slug})
    placements = result.structuredContent["placements"]

    assert result.isError is False
    assert {ticket["placement_mode"] for ticket in placements} == {"dynamic_key"}
    assert all(ticket["allowed_keys"] == ["first", "second"] for ticket in placements)
    assert all(
        ticket["required_scope"] == 'with overmind.task(<selected key>, unit="turn"): ...'
        for ticket in placements
    )
    assert all(
        ticket["required_spans"][0]["required_decorator"] == "@overmind.observe()"
        for ticket in placements
    )


def test_plan_required_spans_map_kinds_in_sequence_and_deduplicate():
    context = _context()
    capability = _capability(context.project)
    anchors = [
        {
            "qualname": "app.agent.run",
            "kind": "entry_point",
            "file": "app/agent.py#L10-L12",
        },
        {
            "qualname": "app.agent.use_tool",
            "kind": "tool",
            "file": "app/agent.py#L20-L22",
        },
        {
            "qualname": "app.agent.build_workflow",
            "kind": "workflow",
            "file": "app/agent.py#L30-L32",
        },
        {
            "qualname": "app.agent.fetch",
            "kind": "retrieval",
            "file": "app/agent.py#L40-L42",
        },
        {
            "qualname": "app.agent.nested_entry",
            "kind": "entry_point",
            "file": "app/agent.py#L50-L52",
        },
        {
            "qualname": "app.agent.helper",
            "kind": "function",
            "file": "app/agent.py#L60-L62",
        },
        {
            "qualname": "app.agent.call_model",
            "kind": "llm_call",
            "file": "app/agent.py#L70-L72",
        },
        {
            "qualname": "app.agent.other",
            "kind": "unknown",
            "file": "app/agent.py#L80-L82",
        },
    ]
    behaviour, _ = _behaviour(
        capability,
        "run_agent",
        entry="app.agent.run",
        file="app/agent.py",
        anchors=anchors,
        anchor_sequence=[
            "app.agent.run",
            "app.agent.use_tool",
            "app.agent.build_workflow",
            "app.agent.fetch",
            "app.agent.nested_entry",
            "app.agent.helper",
            "app.agent.call_model",
            "app.agent.other",
            "app.agent.use_tool",
            "app.agent.run",
        ],
    )

    ticket = _placements(context, {"capability": capability.slug, "behaviour": behaviour.key})[0]

    spans = ticket["required_spans"]
    assert [(span["target"]["qualname"], span["required_decorator"]) for span in spans] == [
        ("app.agent.use_tool", "@overmind.tool()"),
        ("app.agent.build_workflow", "@overmind.workflow()"),
        ("app.agent.fetch", "@overmind.retrieval()"),
        ("app.agent.nested_entry", "@overmind.entry_point()"),
        ("app.agent.helper", "@overmind.observe()"),
        ("app.agent.call_model", "@overmind.observe()"),
        ("app.agent.other", "@overmind.observe()"),
    ]
    assert all(set(span) == {"target", "required_decorator"} for span in spans)
    assert [span["target"]["file"] for span in spans] == ["app/agent.py"] * 7
    assert spans[0]["target"] == {
        "file": "app/agent.py",
        "qualname": "app.agent.use_tool",
        "module": "app.agent",
        "import_line": "from app.agent import use_tool",
    }
    assert ticket["required_scope"] == f'@overmind.run(capability_id="{capability.id}")'


def test_plan_omitting_capability_tickets_all_current_capabilities():
    context = _context()
    first = _capability(context.project, "First")
    second = _capability(context.project, "Second")
    _behaviour(first, "first", entry="app.first.run", file="app/first.py")
    _behaviour(second, "second", entry="app.second.run", file="app/second.py")

    result = _call("get_instrumentation_plan", context, {})
    placements = result.structuredContent["placements"]

    assert result.isError is False
    assert {ticket["capability"] for ticket in placements} == {"First", "Second"}
    assert "plans" not in result.structuredContent
    definitions = {definition.name: definition for definition in CATALOG.definitions()}
    assert "get_instrumentation_plan" in definitions
    assert definitions["get_instrumentation_plan"].read_only is True


def test_plan_rejects_foreign_capability_and_empty_registry():
    context = _context()
    other = _context()
    foreign = _capability(other.project)

    foreign_result = _call("get_instrumentation_plan", context, {"capability": str(foreign.id)})
    empty_result = _call("get_instrumentation_plan", context, {})

    assert foreign_result.isError is True
    assert foreign_result.structuredContent["error"]["code"] == "capability_not_found"
    assert empty_result.isError is False
    assert empty_result.structuredContent["placements"] == []
    assert (
        empty_result.structuredContent["human_action"]["code"] == "instrumentation_registry_empty"
    )
