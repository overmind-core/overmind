"""verify_instrumentation: dry-run bind + grade, zero writes."""

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
from overbae.services.behaviour import binder, dry_run
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db(transaction=True)

SHA = "a" * 40

_GRADE_CLAUSES = {"task", "units", "tool_ops", "provenance", "observations", "delivery"}


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _capability(project, name="A") -> Capability:
    slug = f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}"
    return Capability.objects.create(project=project, name=name, slug=slug)


def _behaviour(capability, key, entry, sequence, grain=Behaviour.Grain.RUN):
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
    BehaviourVersion.objects.create(
        behaviour=behaviour,
        analyzed_sha=SHA,
        contract={
            "entry_anchor": entry,
            "anchor_sequence": sequence,
            "anchors": [
                {"qualname": q, "kind": "function", "file": "app/agent.py"} for q in sequence
            ],
        },
    )
    return behaviour


def _span_dict(
    *, span_id, trace_id, name, parent=None, qualname="", start_ns=1, sha=SHA, extra=None
):
    ns, _, fn = qualname.rpartition(".")
    attrs = dict(extra or {})
    if qualname:
        attrs.update({"code.namespace": ns, "code.function.name": fn})
    return {
        "span_id": span_id,
        "trace_id": trace_id,
        "parent_span_id": parent,
        "name": name,
        "span_type": "entry_point" if parent is None else "llm_call",
        "start_time_ns": start_ns,
        "end_time_ns": start_ns + 1_000_000,
        "resource_attrs": {"vcs.ref.head.revision": sha} if sha else {},
        "attributes": attrs,
        "status_code": 0,
    }


def _mcp_context(project: Project) -> MCPContext:
    user = User.objects.create_user(
        email=f"verify-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    ProjectMembership.objects.create(user=user, project=project)
    token = APIToken(
        scope={
            "scope": "project",
            "resourceIds": [str(project.id)],
            "permission": ["read"],
        }
    )
    return MCPContext(user=user, token=token, project=project)


def _call(name: str, context: MCPContext, arguments: dict):
    return asyncio.run(CATALOG.call(name, arguments, context))


def test_happy_path_declared_key_binds():
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "invoice-triage", "app.agent.run", ["app.agent.run", "app.agent.emit"])
    trace_id = uuid.uuid4().hex
    root_id = uuid.uuid4().hex[:16]
    child_id = uuid.uuid4().hex[:16]
    spans = [
        _span_dict(
            span_id=root_id,
            trace_id=trace_id,
            name="run",
            qualname="app.agent.run",
            extra={"overmind.behaviour.key": "invoice-triage"},
        ),
        _span_dict(
            span_id=child_id,
            trace_id=trace_id,
            name="emit",
            parent=root_id,
            qualname="app.agent.emit",
            start_ns=2,
        ),
    ]
    result = _call(
        "verify_instrumentation",
        _mcp_context(project),
        {"capability": capability.slug, "spans": spans},
    )
    output = result.structuredContent
    assert result.isError is False
    assert output["errors"] == []
    assert output["ok"] is True
    assert len(output["tasks"]) == 1
    task = output["tasks"][0]
    assert task["behaviour_key"] == "invoice-triage"
    assert task["binding_source"] == TaskExecution.BindingSource.DECLARED
    assert task["route_flags"] == []
    assert task["capability"] == capability.name
    assert task["capability_id"] == str(capability.id)
    assert len(output["capabilities"]) == 1
    cap_entry = output["capabilities"][0]
    assert cap_entry["capability"] == capability.name
    assert cap_entry["capability_id"] == str(capability.id)
    assert set(cap_entry["grades"].keys()) == _GRADE_CLAUSES
    assert "grades" not in output
    assert "punch_list" not in output


def test_turn_at_entry_can_join_matching_run_contract():
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "run-task", "app.agent.run", ["app.agent.run"])
    span = _span_dict(
        span_id=uuid.uuid4().hex[:16],
        trace_id=uuid.uuid4().hex,
        name="turn",
        qualname="app.agent.run",
        extra={"overmind.unit_kind": "turn", "overmind.behaviour.key": "run-task"},
    )

    result = dry_run.verify_spans(str(project.id), [span], capability=capability)

    task = result["tasks"][0]
    assert result["ok"] is True
    assert task["behaviour_key"] == "run-task"
    assert task["binding_source"] == TaskExecution.BindingSource.DECLARED
    assert task["route_flags"] == []


def test_matching_run_contract_anchor_join_remains_valid():
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "run-task", "app.agent.run", ["app.agent.run"])
    span = _span_dict(
        span_id=uuid.uuid4().hex[:16],
        trace_id=uuid.uuid4().hex,
        name="run",
        qualname="app.agent.run",
        extra={"overmind.unit_kind": "run"},
    )

    result = dry_run.verify_spans(str(project.id), [span], capability=capability)

    task = result["tasks"][0]
    assert result["ok"] is True
    assert task["behaviour_key"] == "run-task"
    assert task["binding_source"] == TaskExecution.BindingSource.ANCHOR_JOIN
    assert task["route_flags"] == []
    assert task["declared_key"] is None


def test_wrong_declared_key_fails_even_when_anchors_join():
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "run-task", "app.agent.run", ["app.agent.run"])
    span = _span_dict(
        span_id=uuid.uuid4().hex[:16],
        trace_id=uuid.uuid4().hex,
        name="run",
        qualname="app.agent.run",
        extra={"overmind.unit_kind": "run", "overmind.behaviour.key": "other-task"},
    )

    result = dry_run.verify_spans(str(project.id), [span], capability=capability)

    task = result["tasks"][0]
    assert result["ok"] is False
    assert task["declared_key"] == "other-task"
    assert task["binding_source"] == TaskExecution.BindingSource.ANCHOR_JOIN
    assert task["behaviour_key"] == "run-task"


def test_declared_turn_contract_remains_valid():
    project = _project()
    capability = _capability(project)
    _behaviour(
        capability,
        "turn-task",
        "app.agent.turn",
        ["app.agent.turn"],
        grain=Behaviour.Grain.TURN,
    )
    span = _span_dict(
        span_id=uuid.uuid4().hex[:16],
        trace_id=uuid.uuid4().hex,
        name="turn",
        qualname="app.agent.turn",
        extra={"overmind.unit_kind": "turn", "overmind.behaviour.key": "turn-task"},
    )

    result = dry_run.verify_spans(str(project.id), [span], capability=capability)

    task = result["tasks"][0]
    assert result["ok"] is True
    assert task["behaviour_key"] == "turn-task"
    assert task["binding_source"] == TaskExecution.BindingSource.DECLARED
    assert task["route_flags"] == []


def test_punch_list_names_the_missing_evidence():
    project = _project()
    capability = _capability(project)
    span = _span_dict(span_id=uuid.uuid4().hex[:16], trace_id=uuid.uuid4().hex, name="run")
    result = dry_run.verify_spans(str(project.id), [span], capability=capability)
    punch = {
        entry["grade"]: entry["instruction"] for entry in result["capabilities"][0]["punch_list"]
    }
    assert result["ok"] is False
    assert punch.keys() == _GRADE_CLAUSES
    assert "overmind.unit_kind" in punch["units"]
    assert "overmind.eval_context" in punch["observations"]


def test_zero_writes():
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "invoice-triage", "app.agent.run", ["app.agent.run", "app.agent.emit"])
    trace_id = uuid.uuid4().hex
    root_id = uuid.uuid4().hex[:16]
    spans = [
        _span_dict(
            span_id=root_id,
            trace_id=trace_id,
            name="run",
            qualname="app.agent.run",
            extra={"overmind.behaviour.key": "invoice-triage"},
        )
    ]
    execution_count = TaskExecution.objects.count()
    span_count = Span.objects.count()
    profile_count = EvidenceProfile.objects.count()
    result = _call(
        "verify_instrumentation",
        _mcp_context(project),
        {"capability": capability.slug, "spans": spans},
    )
    assert result.isError is False
    assert result.structuredContent["errors"] == []
    assert TaskExecution.objects.count() == execution_count
    assert Span.objects.count() == span_count
    assert EvidenceProfile.objects.count() == profile_count


def test_malformed_span_reports_error_without_raising():
    project = _project()
    capability = _capability(project)
    spans = [{"trace_id": "no-span-id-or-name"}]
    result = _call(
        "verify_instrumentation",
        _mcp_context(project),
        {"capability": capability.slug, "spans": spans},
    )
    output = result.structuredContent
    assert result.isError is False
    assert output["tasks"] == []
    assert output["ok"] is False
    assert len(output["errors"]) == 1
    assert output["errors"][0]["index"] == 0


def test_units_resolve_different_capabilities_via_capability_id():
    project = _project()
    capability_a = _capability(project, name="Agent A")
    capability_b = _capability(project, name="Agent B")
    trace_a, trace_b = uuid.uuid4().hex, uuid.uuid4().hex
    span_a = _span_dict(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_a,
        name="run",
        extra={"overmind.capability.id": str(capability_a.id)},
    )
    span_b = _span_dict(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_b,
        name="run",
        extra={"overmind.capability.id": str(capability_b.id)},
    )
    result = dry_run.verify_spans(str(project.id), [span_a, span_b])
    assert result["errors"] == []
    assert len(result["tasks"]) == 2
    assert {t["capability"] for t in result["tasks"]} == {"Agent A", "Agent B"}
    assert len(result["capabilities"]) == 2
    for entry in result["capabilities"]:
        assert set(entry["grades"].keys()) == _GRADE_CLAUSES


def test_unit_inherits_capability_from_ancestor_span():
    project = _project()
    capability = _capability(project, name="Agent A")
    trace_id = uuid.uuid4().hex
    root_id = uuid.uuid4().hex[:16]
    turn_id = uuid.uuid4().hex[:16]
    spans = [
        _span_dict(
            span_id=root_id,
            trace_id=trace_id,
            name="root",
            extra={"overmind.capability.id": str(capability.id)},
        ),
        _span_dict(
            span_id=turn_id,
            trace_id=trace_id,
            name="turn",
            parent=root_id,
            start_ns=2,
            extra={"overmind.unit_kind": "turn"},
        ),
    ]
    result = dry_run.verify_spans(str(project.id), spans)
    assert result["errors"] == []
    turn_task = next(t for t in result["tasks"] if t["unit_span_id"] == turn_id)
    assert turn_task["capability"] == "Agent A"
    assert turn_task["capability_id"] == str(capability.id)


def test_turn_slices_include_run_surface():
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "run-task", "app.agent.run", ["app.agent.run"])
    trace_id = uuid.uuid4().hex
    run_id = uuid.uuid4().hex[:16]
    turn_id = uuid.uuid4().hex[:16]
    spans = [
        _span_dict(
            span_id=run_id,
            trace_id=trace_id,
            name="run",
            qualname="app.agent.run",
            extra={"overmind.unit_kind": "run"},
        ),
        _span_dict(
            span_id=turn_id,
            trace_id=trace_id,
            name="turn",
            parent=run_id,
            start_ns=2,
            extra={"overmind.unit_kind": "turn"},
        ),
    ]
    result = dry_run.verify_spans(str(project.id), spans, capability=capability)
    by_unit = {t["unit_span_id"]: t for t in result["tasks"]}
    assert turn_id in by_unit
    assert run_id in by_unit
    assert by_unit[run_id]["behaviour_key"] == "run-task"


def test_turn_slices_omit_unbound_run_surface():
    project = _project()
    capability = _capability(project)
    _behaviour(
        capability,
        "turn-task",
        "app.agent.turn",
        ["app.agent.turn"],
        grain=Behaviour.Grain.TURN,
    )
    trace_id = uuid.uuid4().hex
    run_id = uuid.uuid4().hex[:16]
    turn_id = uuid.uuid4().hex[:16]
    spans = [
        _span_dict(
            span_id=run_id,
            trace_id=trace_id,
            name="run",
            extra={"overmind.unit_kind": "run"},
        ),
        _span_dict(
            span_id=turn_id,
            trace_id=trace_id,
            name="turn",
            parent=run_id,
            start_ns=2,
            qualname="app.agent.turn",
            extra={"overmind.unit_kind": "turn", "overmind.behaviour.key": "turn-task"},
        ),
    ]
    result = dry_run.verify_spans(str(project.id), spans, capability=capability)
    by_unit = {t["unit_span_id"]: t for t in result["tasks"]}
    assert turn_id in by_unit
    assert run_id not in by_unit
    assert result["ok"] is True


def test_unit_with_no_resolvable_identity_yields_null_capability():
    project = _project()
    span = _span_dict(span_id=uuid.uuid4().hex[:16], trace_id=uuid.uuid4().hex, name="run")
    result = dry_run.verify_spans(str(project.id), [span])
    assert result["errors"] == []
    assert len(result["tasks"]) == 1
    task = result["tasks"][0]
    assert task["capability"] is None
    assert task["capability_id"] is None
    assert task["behaviour_key"] is None
    assert len(result["capabilities"]) == 1
    assert result["capabilities"][0]["capability"] is None
    assert result["capabilities"][0]["capability_id"] is None


def test_sha_read_from_span_attributes_when_resource_attrs_lack_it():
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "invoice-triage", "app.agent.run", ["app.agent.run"])
    span = _span_dict(
        span_id=uuid.uuid4().hex[:16],
        trace_id=uuid.uuid4().hex,
        name="run",
        qualname="app.agent.run",
        sha="",
        extra={
            "vcs.ref.head.revision": SHA,
            "overmind.behaviour.key": "invoice-triage",
        },
    )
    result = dry_run.verify_spans(str(project.id), [span], capability=capability)
    assert result["tasks"][0]["behaviour_key"] == "invoice-triage"
    assert result["tasks"][0]["route_flags"] == []


def test_sha_drift_dry_run_is_read_only():
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "invoice-triage", "app.agent.run", ["app.agent.run"])
    span = _span_dict(
        span_id=uuid.uuid4().hex[:16],
        trace_id=uuid.uuid4().hex,
        name="run",
        qualname="app.agent.run",
        sha="b" * 40,
    )
    result = dry_run.verify_spans(str(project.id), [span], capability=capability)

    assert result["ok"] is True
    assert result["tasks"][0]["behaviour_key"] == "invoice-triage"
    assert binder.FLAG_SHA_DRIFT in result["tasks"][0]["route_flags"]
