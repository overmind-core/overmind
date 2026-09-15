from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import (
    Behaviour,
    BehaviourVersion,
    Capability,
    EvalSet,
    EvalSetMember,
    Evaluator,
    Project,
    ProjectMembership,
    ScoringPass,
    Span,
    TaskExecution,
    User,
)
from overbae.services.behaviour import binder, scoring
from overbae.services.behaviour.anchoring import reanchor, refresh_grains
from overbae.services.behaviour.coverage import behaviour_coverage
from overbae.services.behaviour.registry import behaviour_contracts_from_card
from overbae.services.codebase.anchors import source_qualnames, verify_analysis_anchors
from overbae.services.eval import card_compiler
from overbae.services.eval.grounding import EvalGroundingContext

pytestmark = pytest.mark.django_db

SHA = "a" * 40


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _capability(project) -> Capability:
    return Capability.objects.create(project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}")


def _behaviour(
    capability, key, entry, sequence, terminal_kind="emits_record", sha=SHA, claim="code_path"
):
    behaviour = Behaviour.objects.create(
        project=capability.project,
        capability=capability,
        key=key,
        display_name=key,
        entry_anchor=entry,
        first_seen_sha=sha,
        last_seen_sha=sha,
    )
    BehaviourVersion.objects.create(
        behaviour=behaviour,
        analyzed_sha=sha,
        contract={
            "key": key,
            "entry_anchor": entry,
            "claim": claim,
            "anchor_sequence": sequence,
            "anchors": [
                {"qualname": q, "kind": "function", "file": "app/agent.py#L1-L10"} for q in sequence
            ],
            "terminal": {"kind": terminal_kind, "description": ""},
        },
    )
    # Grain is a scanned column; fixtures write it the way the scan does.
    refresh_grains(capability)
    behaviour.refresh_from_db()
    return behaviour


def _span(
    project,
    capability,
    *,
    trace_id,
    qualname="",
    sha=SHA,
    parent=None,
    start_ns=1,
    extra_attrs=None,
):
    ns, _, fn = qualname.rpartition(".")
    attrs = {}
    if qualname:
        attrs = {"code.namespace": ns, "code.function.name": fn}
    if extra_attrs:
        attrs.update(extra_attrs)
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=parent,
        project=project,
        capability=capability,
        start_time_ns=start_ns,
        end_time_ns=start_ns + 1_000_000,
        resource_attrs={"vcs.ref.head.revision": sha} if sha else {},
        attributes=attrs,
    )


def _bind(project, capability, qualnames, *, sha=SHA, extra_attrs=None):
    trace_id = uuid.uuid4().hex
    root = _span(
        project,
        capability,
        trace_id=trace_id,
        qualname=qualnames[0],
        sha=sha,
        extra_attrs=extra_attrs,
    )
    spans = [root] + [
        _span(
            project,
            capability,
            trace_id=trace_id,
            qualname=q,
            sha=sha,
            parent=root.span_id,
            start_ns=(i + 2),
        )
        for i, q in enumerate(qualnames[1:])
    ]
    return binder.bind_execution(
        unit_span=root, unit_spans=spans, capability=capability, project_id=str(project.id)
    )


def test_interior_anchor_join_binds_and_records_evidence():
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(
        capability, "happy", "app.agent.run", ["app.agent.run", "app.agent.emit"]
    )
    execution = _bind(project, capability, ["app.agent.run", "app.agent.emit"])
    assert execution.behaviour_id == behaviour.id
    assert execution.binding_source == TaskExecution.BindingSource.ANCHOR_JOIN
    assert execution.observed_route["matched_anchors"] == ["app.agent.emit", "app.agent.run"]


def test_sole_behaviour_binds_despite_entry_qualname_drift():
    """Module-naming drift must not park the execution; suffix-tolerant matching
    still recovers the drifted interior anchors as evidence."""
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(
        capability, "happy", "pkg.agent.run", ["pkg.agent.run", "pkg.agent.emit"]
    )
    execution = _bind(project, capability, ["agent.run", "agent.emit"])  # runtime import drift
    assert execution.behaviour_id == behaviour.id
    assert execution.observed_route["matched_anchors"] == ["pkg.agent.emit", "pkg.agent.run"]


def test_unscanned_sha_binds_against_current_registry_with_drift_flag():
    """A local/unpushed commit never gets versions (a scan only mints the remote
    head): it binds against the current registry, flagged, instead of parking."""
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "happy", "app.agent.run", ["app.agent.run"], sha="b" * 40)
    execution = _bind(project, capability, ["app.agent.run"], sha=SHA)  # no version for SHA
    assert execution.behaviour_id == behaviour.id
    assert execution.binding_source == TaskExecution.BindingSource.ANCHOR_JOIN
    assert binder.FLAG_SHA_DRIFT in execution.route_flags
    assert execution.observed_route["contract_sha"] == "b" * 40


def test_missing_sha_binds_against_current_registry():
    """SHA absence is a flag, not Unbound."""
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "happy", "app.agent.run", ["app.agent.run"])
    execution = _bind(project, capability, ["app.agent.run"], sha="")
    assert execution.behaviour_id == behaviour.id
    assert execution.binding_source == TaskExecution.BindingSource.ANCHOR_JOIN
    assert binder.FLAG_NO_SHA in execution.route_flags
    assert execution.observed_route["contract_sha"] == SHA


def test_inferred_vcs_sha_is_a_noop_without_a_repo():
    project = _project()
    assert binder.attach_inferred_vcs_sha(str(project.id), {}) == {}
    client = {binder.VCS_SHA: "e" * 40}
    assert binder.attach_inferred_vcs_sha(str(project.id), client) == client
    assert binder.project_vcs_sha(str(project.id)) == ""


def test_exact_sha_version_beats_newer_registry_version():
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "happy", "app.agent.run", ["app.agent.run"], sha=SHA)
    newer = BehaviourVersion.objects.create(
        behaviour=behaviour,
        analyzed_sha="c" * 40,
        contract=behaviour.versions.get().contract,
    )
    execution = _bind(project, capability, ["app.agent.run"], sha=SHA)
    assert execution.behaviour_version_id != newer.id
    assert execution.behaviour_version.analyzed_sha == SHA
    assert binder.FLAG_SHA_DRIFT not in execution.route_flags


def test_empty_registry_parks_unbound():
    project = _project()
    capability = _capability(project)
    execution = _bind(project, capability, ["app.agent.run"], sha=SHA)
    assert execution.behaviour_id is None
    assert binder.FLAG_UNANALYZED_SHA in execution.route_flags


def test_unanalyzed_sha_does_not_queue_server_scan():
    """Server Cursor scan is gone — an unknown SHA only parks the binding."""
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "happy", "app.agent.run", ["app.agent.run"], sha="b" * 40)
    execution = _bind(project, capability, ["app.agent.run"], sha=SHA)
    # Unknown SHA parks unbound; no server scan is queued.
    assert (
        binder.FLAG_SHA_DRIFT in execution.route_flags
        or binder.FLAG_UNANALYZED_SHA in execution.route_flags
    )


def test_binding_is_idempotent_one_row():
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "happy", "app.agent.run", ["app.agent.run"])
    trace_id = uuid.uuid4().hex
    root = _span(project, capability, trace_id=trace_id, qualname="app.agent.run")
    first = binder.bind_execution(
        unit_span=root, unit_spans=[root], capability=capability, project_id=str(project.id)
    )
    second = binder.bind_execution(
        unit_span=root, unit_spans=[root], capability=capability, project_id=str(project.id)
    )
    assert first.id == second.id
    assert TaskExecution.objects.filter(project=project).count() == 1


def test_bind_reads_legacy_conversation_id_on_unit_span():
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "happy", "app.agent.run", ["app.agent.run"])
    trace_id = uuid.uuid4().hex
    root = _span(project, capability, trace_id=trace_id, qualname="app.agent.run")
    root.attributes = {**(root.attributes or {}), "overmind.conversation.id": "sess-legacy"}
    root.save(update_fields=["attributes"])
    execution = binder.bind_execution(
        unit_span=root, unit_spans=[root], capability=capability, project_id=str(project.id)
    )
    assert execution.conversation_id == "sess-legacy"


def test_bind_reads_conversation_id_from_child_span():
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "happy", "app.agent.run", ["app.agent.run", "app.agent.emit"])
    trace_id = uuid.uuid4().hex
    root = _span(project, capability, trace_id=trace_id, qualname="app.agent.run")
    child = _span(
        project,
        capability,
        trace_id=trace_id,
        qualname="app.agent.emit",
        parent=root.span_id,
        start_ns=2,
    )
    child.attributes = {**(child.attributes or {}), "conversation.id": "sess-child"}
    child.save(update_fields=["attributes"])
    execution = binder.bind_execution(
        unit_span=root, unit_spans=[root, child], capability=capability, project_id=str(project.id)
    )
    assert execution.conversation_id == "sess-child"


def test_interior_anchors_disambiguate_between_behaviours():
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "path-a", "app.agent.run", ["app.agent.run", "app.agent.fast"])
    path_b = _behaviour(capability, "path-b", "app.agent.run", ["app.agent.run", "app.agent.slow"])
    execution = _bind(project, capability, ["app.agent.run", "app.agent.slow"])
    assert execution.behaviour_id == path_b.id
    assert execution.observed_route["matched_anchors"] == ["app.agent.run", "app.agent.slow"]


def test_interior_anchor_tie_parks_unbound():
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "path-a", "app.agent.run", ["app.agent.run", "app.agent.fast"])
    _behaviour(capability, "path-b", "app.agent.run", ["app.agent.run", "app.agent.slow"])
    execution = _bind(project, capability, ["app.agent.run"])  # nothing discriminates
    assert execution.behaviour_id is None
    assert binder.FLAG_AMBIGUOUS in execution.route_flags


def test_ancestor_anchors_disambiguate_turn_units():
    """A turn-carved unit's subtree may expose nothing to any contract; its
    enclosing pipeline spans are the route evidence that picks the behaviour."""
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "deep", "app.deep.run", ["app.deep.run"])
    standard = _behaviour(
        capability,
        "standard",
        "app.agent.conduct",
        ["app.agent.conduct", "app.agent.report"],
    )
    trace_id = uuid.uuid4().hex
    pipeline = _span(project, capability, trace_id=trace_id, qualname="app.agent.conduct")
    unit = _span(
        project,
        capability,
        trace_id=trace_id,
        qualname="app.agent._process_sub_query",
        parent=pipeline.span_id,
        start_ns=2,
    )
    child = _span(
        project,
        capability,
        trace_id=trace_id,
        qualname="app.tools.scrape",
        parent=unit.span_id,
        start_ns=3,
    )
    execution = binder.bind_execution(
        unit_span=unit,
        unit_spans=[unit, child],
        capability=capability,
        project_id=str(project.id),
        ancestor_spans=[pipeline],
    )
    assert execution.behaviour_id == standard.id
    assert execution.observed_route["ancestors"] == ["app.agent.conduct"]
    assert execution.observed_route["matched_anchors"] == ["app.agent.conduct"]


def test_entry_anchor_breaks_interior_tie():
    """A turn-carved unit's interior is often invisible to every contract —
    its entry qualname is the only contract-known step and must break the
    zero-overlap tie when it hits exactly one candidate."""
    project = _project()
    capability = _capability(project)
    _behaviour(capability, "research", "app.agent.plan", ["app.agent.plan", "app.agent.sub_query"])
    writing = _behaviour(
        capability, "writing", "app.agent.write", ["app.agent.write", "app.tools.render"]
    )
    execution = _bind(project, capability, ["app.agent.write", "app.tools.fetch"])
    assert execution.behaviour_id == writing.id
    assert execution.binding_source == TaskExecution.BindingSource.ANCHOR_JOIN
    assert execution.observed_route["matched_anchors"] == ["app.agent.write"]


def test_most_specific_inclusion_beats_parent_overlap():
    """A turn whose route includes a child contract binds the child, not the
    larger parent that also overlaps. Parent grain is run (decision_surface);
    child is turn (code_path sibling of that surface)."""
    project = _project()
    capability = _capability(project)
    capability.entrypoint_fn = "app.agent.run"
    capability.save(update_fields=["entrypoint_fn"])
    parent = _behaviour(
        capability,
        "lead-loop",
        "app.agent.run",
        ["app.agent.run", "app.agent.think", "app.agent.act"],
        claim="decision_surface",
    )
    child = _behaviour(
        capability, "startup", "app.agent.run", ["app.agent.run", "app.agent.assemble"]
    )
    startup_only = _bind(project, capability, ["app.agent.assemble"])
    assert startup_only.behaviour_id == child.id
    assert startup_only.binding_source == TaskExecution.BindingSource.ANCHOR_JOIN

    both = _bind(
        project,
        capability,
        ["app.agent.assemble", "app.agent.think"],
        extra_attrs={"overmind.unit_kind": "turn"},
    )
    assert both.behaviour_id == child.id

    run_unit = _bind(
        project,
        capability,
        ["app.agent.run", "app.agent.assemble", "app.agent.think", "app.agent.act"],
        extra_attrs={"overmind.unit_kind": "run"},
    )
    assert run_unit.behaviour_id == parent.id
    assert "app.agent.assemble" in run_unit.observed_route["matched_anchors"]


def test_missing_sha_still_binds_after_identity():
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(
        capability, "happy", "app.agent.run", ["app.agent.run", "app.agent.emit"]
    )
    execution = _bind(project, capability, ["app.agent.run", "app.agent.emit"], sha="")
    assert execution.behaviour_id == behaviour.id
    assert binder.FLAG_NO_SHA in execution.route_flags
    assert execution.observed_route["contract_sha"] == SHA


def test_declared_key_wins_over_structural():
    project = _project()
    capability = _capability(project)
    _behaviour(
        capability,
        "loop",
        "app.agent.run",
        ["app.agent.run", "app.agent.think"],
        claim="decision_surface",
    )
    declared = _behaviour(capability, "startup", "app.agent.run", ["app.agent.assemble"])
    execution = _bind(
        project,
        capability,
        ["app.agent.run", "app.agent.think"],
        extra_attrs={"overmind.behaviour.key": "startup"},
    )
    assert execution.behaviour_id == declared.id
    assert execution.binding_source == TaskExecution.BindingSource.DECLARED

    aliased = _bind(
        project,
        capability,
        ["app.agent.run", "app.agent.think"],
        extra_attrs={"overmind.task": "startup"},
    )
    assert aliased.behaviour_id == declared.id
    assert aliased.binding_source == TaskExecution.BindingSource.DECLARED


def _loop_and_leaf(project):
    capability = _capability(project)
    capability.entrypoint_fn = "app.agent.run"
    capability.save(update_fields=["entrypoint_fn"])
    loop = _behaviour(
        capability,
        "loop",
        "app.agent.run",
        ["app.agent.run", "app.agent.think"],
        claim="decision_surface",
    )
    _behaviour(capability, "portfolio-manager", "app.agent.decide", ["app.agent.decide"])
    return capability, loop


def test_run_unit_never_declared_binds_to_turn_grain_behaviour():
    """An interior phase's key stamped on the run boundary must not grade the
    whole run against that phase — the structural join decides instead."""
    project = _project()
    capability, loop = _loop_and_leaf(project)
    execution = _bind(
        project,
        capability,
        ["app.agent.run", "app.agent.think"],
        extra_attrs={"overmind.unit_kind": "run", "overmind.behaviour.key": "portfolio-manager"},
    )
    assert execution.behaviour_id == loop.id
    assert execution.binding_source == TaskExecution.BindingSource.ANCHOR_JOIN
    assert binder.FLAG_DECLARED_GRAIN_MISMATCH in execution.route_flags


def test_contract_key_adopts_matching_mode_slug():
    """A trajectory path describing a modes[] entry keys on that mode's slug,
    whatever id the analyzer emitted."""
    from overbae.services.behaviour.registry import behaviour_contracts_from_card

    card = {
        "anchors": [{"qualname": "app.agent.run", "kind": "entry_point"}],
        "modes": [{"name": "Q&A Mode", "entrypoint_fn": "app.agent.run"}],
        "trajectory_map": [
            {
                "id": "qa-flow",
                "name": "Q&A Mode",
                "claim": "declared_task",
                "anchors": ["app.agent.run"],
            }
        ],
    }
    contracts = behaviour_contracts_from_card(card)
    assert [c["key"] for c in contracts] == ["qa-mode"]


def test_run_unit_binds_turn_grain_behaviour_when_entry_anchor_matches():
    """The scan can grade a capability's primary entrypoint as turn while
    production carves the request as one run unit — the entry anchor decides,
    in both grain directions."""
    project = _project()
    capability, _ = _loop_and_leaf(project)
    execution = _bind(
        project,
        capability,
        ["app.agent.decide"],
        extra_attrs={"overmind.unit_kind": "run"},
    )
    assert execution.behaviour is not None
    assert execution.behaviour.key == "portfolio-manager"
    assert execution.binding_source == TaskExecution.BindingSource.ANCHOR_JOIN


def test_entry_point_unit_counts_as_run_grain_for_declared_binding():
    """An entry_point span is a capability invocation even when the SDK
    predates unit_kind — the same guard applies."""
    project = _project()
    capability, loop = _loop_and_leaf(project)
    execution = _bind(
        project,
        capability,
        ["app.agent.run", "app.agent.think"],
        extra_attrs={
            "overmind.span.type": "entry_point",
            "overmind.behaviour.key": "portfolio-manager",
        },
    )
    assert execution.behaviour_id == loop.id
    assert execution.binding_source == TaskExecution.BindingSource.ANCHOR_JOIN
    assert binder.FLAG_DECLARED_GRAIN_MISMATCH in execution.route_flags


def test_run_unit_declaring_its_run_grain_behaviour_keeps_declared_binding():
    project = _project()
    capability, loop = _loop_and_leaf(project)
    execution = _bind(
        project,
        capability,
        ["app.agent.run", "app.agent.think"],
        extra_attrs={"overmind.unit_kind": "run", "overmind.behaviour.key": "loop"},
    )
    assert execution.behaviour_id == loop.id
    assert execution.binding_source == TaskExecution.BindingSource.DECLARED
    assert binder.FLAG_DECLARED_GRAIN_MISMATCH not in execution.route_flags


def test_turn_unit_declaring_turn_grain_behaviour_keeps_declared_binding():
    project = _project()
    capability, _ = _loop_and_leaf(project)
    startup = _behaviour(capability, "startup", "app.agent.run", ["app.agent.assemble"])
    execution = _bind(
        project,
        capability,
        ["app.agent.assemble"],
        extra_attrs={"overmind.unit_kind": "turn", "overmind.behaviour.key": "startup"},
    )
    assert execution.behaviour_id == startup.id
    assert execution.binding_source == TaskExecution.BindingSource.DECLARED


def test_handoff_turn_binds_run_grain_behaviour_at_its_entry():
    project = _project()
    capability = _capability(project)
    capability.entrypoint_fn = "app.b.review"
    capability.save(update_fields=["entrypoint_fn"])
    review = _behaviour(
        capability,
        "review",
        "app.b.review",
        ["app.b.review"],
        claim="decision_surface",
    )
    execution = _bind(
        project,
        capability,
        ["app.b.review"],
        extra_attrs={"overmind.unit_kind": "turn", "overmind.behaviour.key": "review"},
    )
    assert execution.behaviour_id == review.id
    assert execution.binding_source == TaskExecution.BindingSource.DECLARED
    assert binder.FLAG_DECLARED_GRAIN_MISMATCH not in execution.route_flags
    assert binder.FLAG_GRAIN_MISMATCH not in execution.route_flags


def test_span_capability_id_wins():
    """Process resource identity is the first init(); span overmind.capability.id
    is the unit's capability. Bind against that capability's tasks."""
    project = _project()
    agent_run = _capability(project)
    suggestions = Capability.objects.create(
        project=project, name="Follow-up Suggestions", slug="follow-up-suggestions"
    )
    _behaviour(agent_run, "loop", "app.agent.run", ["app.agent.run"], claim="decision_surface")
    task = _behaviour(suggestions, "generate-suggestions", "app.suggest.run", ["app.suggest.run"])
    execution = _bind(
        project,
        agent_run,
        ["app.suggest.run"],
        extra_attrs={"overmind.capability.id": str(suggestions.id)},
    )
    assert execution.capability_id == suggestions.id
    assert execution.behaviour_id == task.id


def test_indistinguishable_pairs_flagged():
    card = {
        "anchors": [
            {"qualname": "m.run", "kind": "entry_point", "file": "m.py#L1-L5"},
            {"qualname": "m.emit", "kind": "function", "file": "m.py#L6-L9"},
        ],
        "trajectory_map": [
            {"id": "a", "anchors": ["m.run", "m.emit"], "terminal": {"kind": "emits_record"}},
            {"id": "b", "anchors": ["m.run", "m.emit"], "terminal": {"kind": "emits_record"}},
            {"id": "c", "anchors": ["m.run"], "terminal": {"kind": "error_exit"}},
        ],
    }
    contracts = {c["key"]: c for c in behaviour_contracts_from_card(card)}
    assert [f["behaviour_key"] for f in contracts["a"]["indistinguishable_with"]] == ["b"]
    assert [f["behaviour_key"] for f in contracts["b"]["indistinguishable_with"]] == ["a"]
    assert contracts["c"]["indistinguishable_with"] == []


TOOL_SPEC = [
    {"name": "Fetch-Data", "purpose": "pull rows", "side_effect": "read"},
    {"name": "post_update", "purpose": "write result", "side_effect": "write"},
]


def test_contract_carries_canonical_tool_set():
    card = {
        "anchors": [{"qualname": "m.run", "kind": "entry_point", "file": "m.py#L1-L5"}],
        "tool_spec": TOOL_SPEC,
        "trajectory_map": [
            {
                "id": "happy",
                "anchors": ["m.run"],
                # fetch_data joins Fetch-Data after canonicalization; the
                # helper is an internal callable outside tool_spec.
                "tools": ["fetch_data", "m.helpers.transform", "post_update"],
                "terminal": {"kind": "emits_record"},
            }
        ],
    }
    (contract,) = behaviour_contracts_from_card(card)
    assert contract["tool_set"] == [
        {
            "name": "fetch_data",
            "declared_name": "Fetch-Data",
            "purpose": "pull rows",
            "side_effect": "read",
        },
        {
            "name": "post_update",
            "declared_name": "post_update",
            "purpose": "write result",
            "side_effect": "write",
        },
    ]
    assert contract["tools"] == ["fetch_data", "m.helpers.transform", "post_update"]


def test_contract_carries_backbone_steps_and_counts_may_use_tools():
    card = {
        "anchors": [{"qualname": "m.run", "kind": "entry_point", "file": "m.py#L1-L5"}],
        "tool_spec": TOOL_SPEC,
        "trajectory_map": [
            {
                "id": "answer",
                "anchors": ["m.run"],
                "sequence": ["check scope", "gather evidence"],
                "steps": [
                    {"step": "check scope", "anchors": ["m.run"], "may_use": []},
                    {
                        "step": "gather evidence",
                        "anchors": [],
                        "may_use": [{"tool": "Fetch-Data", "when": "rows are needed"}],
                    },
                ],
                "tools": ["post_update"],
                "terminal": {"kind": "emits_record"},
            }
        ],
    }
    (contract,) = behaviour_contracts_from_card(card)
    assert contract["steps"][1]["may_use"] == [{"tool": "Fetch-Data", "when": "rows are needed"}]
    # A conditional capability is still an expected tool of the task.
    assert [t["name"] for t in contract["tool_set"]] == ["post_update", "fetch_data"]

    specs = card_compiler.compile_behaviour_suites(EvalGroundingContext(codebase_card=card))
    names = {s.name for s in specs}
    assert "behaviour-answer-success" in names
    assert "behaviour-answer-unexpected-tools" not in names
    assert "behaviour-answer-tool-coverage" not in names


def test_compile_behaviour_suites_does_not_mint_tool_conformance_evals():
    card = {
        "tool_spec": TOOL_SPEC,
        "trajectory_map": [
            {
                "id": "happy",
                "anchors": ["m.run", "m.emit"],
                "tools": ["Fetch-Data", "m.helpers.transform"],
                "terminal": {"kind": "emits_record"},
            },
            {
                "id": "internal-only",
                "anchors": ["m.run"],
                "tools": ["m.helpers.transform"],
                "terminal": {"kind": "returns_empty"},
            },
        ],
    }
    specs = card_compiler.compile_behaviour_suites(EvalGroundingContext(codebase_card=card))
    names = {s.name for s in specs}
    assert "behaviour-happy-success" in names
    assert "behaviour-internal-only-success" in names
    assert not any("unexpected-tools" in n or "tool-coverage" in n for n in names)


def test_compile_managed_does_not_mint_session_or_task_state_evals():
    card = {
        "tool_spec": TOOL_SPEC,
        "trajectory_map": [
            {
                "id": "happy",
                "anchors": ["m.run"],
                "terminal": {"kind": "emits_record"},
            }
        ],
    }
    specs = card_compiler.compile_managed_card_evaluators(EvalGroundingContext(codebase_card=card))
    names = {s.name for s in specs}
    assert not any("session" in n or "task-state" in n or "task_state" in n for n in names)
    assert not any("unexpected-tools" in n or "tool-coverage" in n for n in names)
    assert "checkpoint-coverage" not in names
    assert "trajectory-terminals" not in names
    assert any(n.endswith("-success") for n in names)


def test_compile_managed_follows_construct_gate():
    card = {
        "output_schema": {"properties": {"text": {}}, "required_keys": ["text"]},
        "tool_spec": [{"name": "search"}],
        "trajectory_map": [
            {
                "id": "happy",
                "anchors": ["m.run"],
                "terminal": {"kind": "emits_record"},
            }
        ],
    }
    specs = card_compiler.compile_managed_card_evaluators(EvalGroundingContext(codebase_card=card))
    names = {s.name for s in specs}
    assert "output-contract-required-keys" in names
    assert "output-schema-field-conformance" not in names
    assert "tool-vocabulary-selection" not in names
    assert not any("tool-coverage" in s.name or "unexpected-tools" in s.name for s in specs)


def test_compile_behaviour_suites_mints_task_success_judge_as_score_driver():
    card = {
        "tool_spec": TOOL_SPEC,
        "trajectory_map": [
            {
                "id": "happy",
                "anchors": ["m.run", "m.mid", "m.emit"],
                "routing": "extract invoices from inbound email",
                "sequence": ["classify email", "extract fields"],
                "terminal": {"kind": "emits_record", "description": "returns records"},
            }
        ],
    }
    specs = card_compiler.compile_behaviour_suites(EvalGroundingContext(codebase_card=card))
    by_name = {s.name: s for s in specs}

    judge = by_name["behaviour-happy-success"]
    assert judge.kind == "llm_judge"
    assert judge.scope == "trajectory"
    assert judge.config["behaviour"] == {
        "behaviour_key": "happy",
        "role": "outcome",
        "anchor_segment": [],
    }
    assert {e.var for e in judge.variable_mapping} == {
        "input",
        "trajectory",
        "tool_calls",
        "output",
    }
    assert "correct refusal" in judge.rubric_md.lower()
    assert "extract invoices from inbound email" in judge.rubric_md
    assert [item.id for item in judge.checklist] == [
        "serves-open-ask",
        "delivered-kind-matches",
        "miss-is-legitimate",
        "evidence-supported-reply",
    ]
    assert judge.checklist[0].gate is True
    assert not any(item.gate for item in judge.checklist[1:])
    blob = " ".join(item.q for item in judge.checklist).lower() + " " + judge.rubric_md.lower()
    assert "openui" not in blob
    assert "invented" not in blob
    assert "do not require any particular tool" in blob
    assert "empty" in blob
    assert "overall score is this one concern" in blob
    assert "asserting details" in blob
    assert "delivered result" in blob
    assert "job attempted" not in blob
    assert "genuine ambiguity" in blob
    assert "wrong kind" in blob
    assert "reported the mismatch" in blob
    # Supersession/confirmation/refusal policy lives in the ledger rubric, not judge prose.
    assert "ledger" in blob
    assert "supersedes the running intent" not in blob
    assert "clears_outstanding" not in blob
    for banned in (
        "must call",
        "required tool",
        "expected tool",
        "may_use",
        "tool-coverage",
        "create_dataset",
        "gated-pause",
        "finetune",
    ):
        assert banned not in blob
    # Terminal-kind string equality is not a pass/fail gate.
    assert "behaviour-happy-outcome" not in by_name
    # A string sequence has no backbone steps — no invented per-anchor-pair judges.
    assert not any(n.startswith("behaviour-happy-step-") for n in by_name)


def _step(label, *, kind="agent_step", anchors=None, may_use=None, **model):
    entry = {
        "step": label,
        "kind": kind,
        "anchors": anchors or [],
        "may_use": may_use or [],
        "input": "",
        "action": "",
        "output": "",
    }
    entry.update(model)
    return entry


def test_compile_behaviour_suites_follows_claim_class():
    card = {
        "tool_spec": TOOL_SPEC,
        "trajectory_map": [
            {
                "id": "extract",
                "claim": "code_path",
                "routing": "isInvoice=true",
                "anchors": ["m.run", "m.mid", "m.emit"],
                "steps": [
                    _step("classify email", anchors=["m.run"]),
                    _step("extract fields", anchors=["m.mid"]),
                    _step("assemble record", anchors=["m.emit"]),
                ],
                "sequence": ["classify email", "extract fields", "assemble record"],
                "terminal": {"kind": "emits_record"},
            },
            {
                "id": "refuse",
                "claim": "declared_task",
                "prompt_quote": "If the request is off-topic, refuse.",
                "routing": "off-topic",
                "anchors": ["m.run"],
                "steps": [_step("refuse politely")],
                "sequence": ["refuse politely"],
                "terminal": {"kind": "returns_empty"},
            },
            {
                "id": "main-loop",
                "claim": "decision_surface",
                "routing": "model chooses tools or answers",
                "anchors": ["m.run", "m.tools"],
                "steps": [
                    _step("assemble turn context", anchors=["m.run"]),
                    _step(
                        "decide: gather evidence or answer",
                        kind="model_invocation",
                        anchors=["m.tools"],
                        input="user + history + tool schemas",
                        action="call tools or answer",
                        output="tool_calls -> dispatch; answer -> respond",
                    ),
                    _step("synthesize answer"),
                ],
                "sequence": [
                    "assemble turn context",
                    "decide: gather evidence or answer",
                    "synthesize answer",
                ],
                "tools": ["Fetch-Data"],
                "terminal": {"kind": "emits_record"},
            },
        ],
    }
    specs = card_compiler.compile_behaviour_suites(EvalGroundingContext(codebase_card=card))
    names = [s.name for s in specs]
    # code_path: outcome + every structured backbone step. No contract checks.
    assert "behaviour-extract-success" in names
    assert "behaviour-extract-step-classify-email" in names
    assert "behaviour-extract-step-extract-fields" in names
    assert "behaviour-extract-step-assemble-record" in names
    # declared_task: outcome against the quoted task; bare unanchored step is
    # not a judged action.
    assert "behaviour-refuse-success" in names
    assert "behaviour-refuse-step-refuse-politely" not in names
    assert "Declared task: If the request is off-topic, refuse." in next(
        s.rubric_md for s in specs if s.name == "behaviour-refuse-success"
    )
    # decision_surface: outcome + architectural steps (anchored prelude +
    # model invocation). Bare synthesize is not judged. Not one eval per tool.
    assert "behaviour-main-loop-success" in names
    assert "behaviour-main-loop-step-assemble-turn-context" in names
    decide = next(
        s for s in specs if s.name == "behaviour-main-loop-step-decide-gather-evidence-or-answer"
    )
    assert decide.config["behaviour"]["anchor_segment"] == ["m.tools"]
    assert {e.var for e in decide.variable_mapping} == {"input", "trajectory", "output"}
    assert "before this one in the trajectory" in decide.checklist[0].q.lower()
    assert decide.checklist[0].gate is True
    assert decide.checklist[1].gate is False
    warranted_q = decide.checklist[0].q.lower()
    assert "quality miss" in warranted_q
    assert "extra off-intent" in warranted_q
    assert "even if it also did" in warranted_q
    assert "skipping a mapped tool" in warranted_q
    assert "Model action: call tools or answer" in decide.rubric_md
    assert "behaviour-main-loop-step-synthesize-answer" not in names
    assert not any("tool-coverage" in n or "unexpected-tools" in n for n in names)
    managed = card_compiler.compile_managed_card_evaluators(
        EvalGroundingContext(codebase_card=card)
    )
    managed_names = {s.name for s in managed}
    assert "checkpoint-coverage" not in managed_names
    assert "trajectory-terminals" not in managed_names
    assert "output-schema-field-conformance" not in managed_names


def _suite(capability, key):
    for name, binding in (
        ("outcome", {"behaviour_key": key, "role": "outcome", "anchor_segment": []}),
        ("step-1", {"behaviour_key": key, "role": "step", "anchor_segment": ["m.run", "m.mid"]}),
        ("step-2", {"behaviour_key": key, "role": "step", "anchor_segment": ["m.mid", "m.emit"]}),
    ):
        Evaluator.objects.create(
            project=capability.project,
            capability=capability,
            name=name,
            kind=Evaluator.Kind.DETERMINISTIC,
            scope=Evaluator.Scope.TRAJECTORY,
            config={"check": "contains", "behaviour": binding},
        )


def _execution(project, capability, behaviour, observed, **kwargs):
    version = behaviour.versions.first()
    return TaskExecution.objects.create(
        project=project,
        capability=capability,
        behaviour=behaviour,
        behaviour_version=version,
        trace_id=uuid.uuid4().hex,
        unit_span_id=uuid.uuid4().hex[:16],
        binding_source=TaskExecution.BindingSource.ANCHOR_JOIN,
        observed_route={"sha": SHA, "anchors": observed, "terminal": "emits_record"},
        **kwargs,
    )


def _scored(outcome, step1=None, step2=None):
    block = {"outcome": {"score": outcome, "outcome": "scored", "passed": None}}
    if step1 is not None:
        block["step-1"] = {"score": step1, "outcome": "scored", "passed": None}
    if step2 is not None:
        block["step-2"] = {"score": step2, "outcome": "scored", "passed": None}
    return block


def test_alternative_route_good_outcome_scores_well():
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "happy", "m.run", ["m.run", "m.mid", "m.emit"])
    _suite(capability, "happy")
    # Alternative route: skipped m.mid entirely, still emitted a great outcome.
    execution = _execution(project, capability, behaviour, ["m.run", "m.emit"])
    scoring.score_execution(execution, _scored(0.9))
    execution.refresh_from_db()
    assert execution.success_score == 0.9
    skipped = {r["evaluator"]: r["outcome"] for r in execution.step_results if r["role"] == "step"}
    assert skipped == {"step-1": "segment_not_run", "step-2": "segment_not_run"}
    assert scoring.FLAG_UNUSUAL_ROUTE_GOOD_OUTCOME in execution.route_flags


def test_usual_route_bad_outcome_scores_poorly():
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "happy", "m.run", ["m.run", "m.mid", "m.emit"])
    _suite(capability, "happy")
    execution = _execution(project, capability, behaviour, ["m.run", "m.mid", "m.emit"])
    scoring.score_execution(execution, _scored(0.1, step1=0.2, step2=0.1))
    execution.refresh_from_db()
    # Weak verdicts compose as an honest mean; no verdict has veto authority.
    assert execution.success_score == pytest.approx((0.1 + 0.2 + 0.1) / 3, abs=1e-4)
    assert {r["evaluator"] for r in execution.step_results if r["role"] == "step"} == {
        "step-1",
        "step-2",
    }
    assert scoring.FLAG_USUAL_ROUTE_BAD_OUTCOME in execution.route_flags


def test_route_metadata_never_gates_score():
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "happy", "m.run", ["m.run", "m.mid", "m.emit"])
    _suite(capability, "happy")
    conforming = _execution(project, capability, behaviour, ["m.run", "m.mid", "m.emit"])
    deviating = _execution(project, capability, behaviour, ["m.run", "m.emit"])
    block = _scored(0.8)  # identical eval evidence for both
    scoring.score_execution(conforming, block)
    scoring.score_execution(deviating, block)
    conforming.refresh_from_db()
    deviating.refresh_from_db()
    assert conforming.success_score == deviating.success_score == 0.8
    align_a = conforming.observed_route["alignment"]
    align_b = deviating.observed_route["alignment"]
    assert align_a["completion"] == 1.0 and align_b["completion"] < 1.0


def test_step_quality_averages_into_composite():
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "happy", "m.run", ["m.run", "m.mid", "m.emit"])
    _suite(capability, "happy")
    execution = _execution(project, capability, behaviour, ["m.run", "m.mid", "m.emit"])
    scoring.score_execution(execution, _scored(1.0, step1=0.5, step2=0.6))
    execution.refresh_from_db()
    assert execution.success_score == pytest.approx((1.0 + 0.5 + 0.6) / 3, abs=1e-4)


def test_failed_step_drags_but_does_not_zero():
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "happy", "m.run", ["m.run", "m.mid", "m.emit"])
    _suite(capability, "happy")
    execution = _execution(project, capability, behaviour, ["m.run", "m.mid", "m.emit"])
    scoring.score_execution(execution, _scored(1.0, step1=0.0, step2=0.9))
    execution.refresh_from_db()
    assert execution.success_score == pytest.approx((1.0 + 0.0 + 0.9) / 3, abs=1e-4)


def test_failed_boolean_dominates_its_scalar():
    """passed=False zeroes that verdict even when the scalar reads high;
    passed=True defers to the graded scalar."""
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "happy", "m.run", ["m.run", "m.mid", "m.emit"])
    _suite(capability, "happy")
    execution = _execution(project, capability, behaviour, ["m.run", "m.mid", "m.emit"])
    scoring.score_execution(
        execution,
        {
            "outcome": {"score": 1.0, "outcome": "scored", "passed": None},
            "step-1": {"score": 0.5, "outcome": "scored", "passed": False},
            "step-2": {"score": 0.9, "outcome": "scored", "passed": True},
        },
    )
    execution.refresh_from_db()
    assert execution.success_score == pytest.approx((1.0 + 0.0 + 0.9) / 3, abs=1e-4)


def test_success_score_is_the_composed_block_score():
    """``success_score`` is the claim-typed composition, never a role-product over step_results."""
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "happy", "m.run", ["m.run", "m.emit"])
    _suite(capability, "happy")
    execution = _execution(project, capability, behaviour, ["m.run", "m.emit"])
    scoring.score_execution(
        execution,
        {
            "outcome": {"score": 1.0, "outcome": "scored", "passed": False},
            "step-1": {"score": 0.9, "outcome": "scored", "passed": True},
        },
    )
    execution.refresh_from_db()
    # passed=False normalizes to 0.0, passed=True defers to the scalar.
    assert execution.success_score == pytest.approx((0.0 + 0.9) / 2, abs=1e-4)


def test_success_score_prefers_persisted_execution_composite():
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "happy", "m.run", ["m.run", "m.emit"])
    _suite(capability, "happy")
    execution = _execution(project, capability, behaviour, ["m.run", "m.emit"])
    scoring.score_execution(
        execution,
        {
            "outcome": {"score": 0.2, "outcome": "scored"},
            "_execution": {"score": 0.97},
        },
    )
    execution.refresh_from_db()
    assert execution.success_score == 0.97


def test_evidence_role_never_composes_into_score():
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "happy", "m.run", ["m.run", "m.emit"])
    Evaluator.objects.create(
        project=project,
        capability=capability,
        name="outcome",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.TRAJECTORY,
        config={
            "check": "contains",
            "behaviour": {"behaviour_key": "happy", "role": "outcome", "anchor_segment": []},
        },
    )
    Evaluator.objects.create(
        project=project,
        capability=capability,
        name="tool-coverage",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.TRAJECTORY,
        config={
            "check": "contains",
            "behaviour": {"behaviour_key": "happy", "role": "evidence", "anchor_segment": []},
        },
    )
    execution = _execution(project, capability, behaviour, ["m.run", "m.emit"])
    scoring.score_execution(
        execution,
        {
            "outcome": {"score": 1.0, "outcome": "scored", "passed": None},
            "tool-coverage": {"score": 0.0, "outcome": "scored", "passed": False},
        },
    )
    execution.refresh_from_db()
    assert execution.success_score == 1.0


def test_segment_ran_tolerates_module_prefix_drift():
    assert scoring.segment_ran(["pkg.agent.run", "pkg.agent.emit"], ["agent.run", "agent.emit"])
    assert not scoring.segment_ran(["pkg.agent.run", "pkg.agent.emit"], ["agent.run"])


def test_step_segment_satisfied_by_ancestor_chain():
    """A turn unit running INSIDE a contract step must keep that step's
    member: the ancestor chain is part of the unit's route evidence."""
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "happy", "m.conduct", ["m.conduct", "m.report"])
    Evaluator.objects.create(
        project=project,
        capability=capability,
        name="gather-step",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.TRAJECTORY,
        config={
            "check": "contains",
            "behaviour": {
                "behaviour_key": "happy",
                "role": "step",
                "anchor_segment": ["m.conduct"],
            },
        },
    )
    eval_set = EvalSet.objects.create(project=project, capability=capability, name="live")
    member = EvalSetMember.objects.create(
        eval_set=eval_set,
        evaluator=Evaluator.objects.get(name="gather-step"),
        role=EvalSetMember.Role.TRACE_SCORING,
        enabled=True,
    )
    execution = _execution(project, capability, behaviour, ["m._process", "m.scrape"])
    assert scoring.filter_members_for_execution([member], execution) == []

    execution.observed_route["ancestors"] = ["m.conduct"]
    execution.save(update_fields=["observed_route"])
    assert scoring.filter_members_for_execution([member], execution) == [member]

    scoring.score_execution(
        execution, {"gather-step": {"score": 1.0, "outcome": "scored", "passed": None}}
    )
    execution.refresh_from_db()
    (step,) = (r for r in execution.step_results if r["evaluator"] == "gather-step")
    assert step["outcome"] == "scored"


def test_per_step_coverage_rollup():
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "happy", "m.run", ["m.run", "m.mid", "m.emit"])
    version = behaviour.versions.get()
    version.contract["claim"] = "code_path"
    version.contract["steps"] = [
        {
            "step": "run",
            "kind": "agent_step",
            "anchors": ["m.run", "m.mid"],
            "may_use": [],
        },
        {
            "step": "emit",
            "kind": "agent_step",
            "anchors": ["m.mid", "m.emit"],
            "may_use": [],
        },
    ]
    version.save(update_fields=["contract"])
    # Only the first backbone step and the outcome are covered.
    for name, binding in (
        ("outcome", {"behaviour_key": "happy", "role": "outcome", "anchor_segment": []}),
        (
            "step-1",
            {"behaviour_key": "happy", "role": "step", "anchor_segment": ["m.run", "m.mid"]},
        ),
    ):
        Evaluator.objects.create(
            project=project,
            capability=capability,
            name=name,
            kind=Evaluator.Kind.DETERMINISTIC,
            scope=Evaluator.Scope.TRAJECTORY,
            config={"check": "contains", "behaviour": binding},
        )
    (entry,) = behaviour_coverage(capability)
    assert entry["behaviour_id"] == str(behaviour.id)
    assert entry["outcome_covered"] is True
    assert entry["outcome_evaluators"] == ["outcome"]
    assert [s["covered"] for s in entry["steps"]] == [True, False]
    assert entry["steps"][1]["segment"] == ["m.mid", "m.emit"]


def test_reanchor_carries_id_by_entry_anchor_on_rename():
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "old-key", "m.run", ["m.run", "m.emit"])
    new_sha = "c" * 40
    outcome = reanchor(
        capability,
        [
            {
                "key": "new-key",
                "name": "renamed",
                "entry_anchor": "m.run",
                "anchor_sequence": ["m.run", "m.emit"],
                "anchors": [],
                "terminal": {"kind": "emits_record"},
            }
        ],
        new_sha,
    )
    assert [b.id for b in outcome["carried"]] == [behaviour.id]
    behaviour.refresh_from_db()
    assert behaviour.key == "new-key"
    assert behaviour.last_seen_sha == new_sha
    assert behaviour.versions.filter(analyzed_sha=new_sha).exists()


def test_reanchor_carries_id_by_lineage_overlap():
    project = _project()
    capability = _capability(project)
    behaviour = _behaviour(capability, "old-key", "m.run", ["m.run", "m.mid", "m.emit"])
    outcome = reanchor(
        capability,
        [
            {
                "key": "new-key",
                "name": "entry renamed too",
                "entry_anchor": "m.run_v2",
                "anchor_sequence": ["m.run_v2", "m.mid", "m.emit"],
                "anchors": [
                    {"qualname": "m.mid", "kind": "function", "file": "app/agent.py#L1-L10"}
                ],
                "terminal": {"kind": "emits_record"},
            }
        ],
        "c" * 40,
    )
    assert [b.id for b in outcome["carried"]] == [behaviour.id]


def test_reanchor_ambiguity_mints_new_and_retires_unreproduced():
    project = _project()
    capability = _capability(project)
    old_a = _behaviour(capability, "a", "m.run", ["m.run", "m.x", "m.emit"])
    old_b = _behaviour(capability, "b", "m.run2", ["m.run2", "m.x", "m.emit"])
    # Equally similar to both (ambiguous lineage) → mints new, retires both.
    outcome = reanchor(
        capability,
        [
            {
                "key": "merged",
                "name": "merged",
                "entry_anchor": "m.run3",
                "anchor_sequence": ["m.x", "m.emit"],
                "anchors": [{"qualname": "m.x", "kind": "function", "file": "app/agent.py#L1-L10"}],
                "terminal": {"kind": "emits_record"},
            }
        ],
        "c" * 40,
    )
    assert len(outcome["minted"]) == 1
    assert {b.id for b in outcome["retired"]} == {old_a.id, old_b.id}
    old_a.refresh_from_db()
    assert old_a.status == Behaviour.Status.RETIRED


def test_reanchor_writes_grain_from_the_scanned_contracts():
    """Grain is written at scan time: the decision surface is run-grain, an
    entry-hitting sibling of a decision surface is turn-grain, a sole
    entry-hitting path is run-grain."""
    project = _project()
    capability = _capability(project)
    capability.entrypoint_fn = "m.run"
    capability.save(update_fields=["entrypoint_fn"])
    contracts = [
        {
            "key": "loop",
            "name": "loop",
            "claim": "decision_surface",
            "entry_anchor": "m.run",
            "anchor_sequence": ["m.run", "m.think"],
            "anchors": [],
            "terminal": {"kind": "emits_record"},
        },
        {
            "key": "startup",
            "name": "startup",
            "claim": "code_path",
            "entry_anchor": "m.run",
            "anchor_sequence": ["m.run", "m.assemble"],
            "anchors": [],
            "terminal": {"kind": "emits_record"},
        },
        {
            "key": "interior",
            "name": "interior",
            "claim": "code_path",
            "entry_anchor": "m.decide",
            "anchor_sequence": ["m.decide"],
            "anchors": [],
            "terminal": {"kind": "emits_record"},
        },
    ]
    reanchor(capability, contracts, SHA)
    grains = {b.key: b.grain for b in Behaviour.objects.filter(capability=capability)}
    assert grains == {"loop": "run", "startup": "turn", "interior": "turn"}

    # A rescan without the decision surface flips the sole entry-hitter to run.
    reanchor(capability, contracts[1:], "c" * 40)
    grains = {
        b.key: b.grain
        for b in Behaviour.objects.filter(capability=capability, status=Behaviour.Status.ACTIVE)
    }
    assert grains == {"startup": "run", "interior": "turn"}


def test_rebind_parked_executions_idempotent(monkeypatch):
    from overbae.tasks import behaviour as behaviour_tasks

    project = _project()
    capability = _capability(project)
    execution = _execution(
        project,
        capability,
        _behaviour(capability, "happy", "m.run", ["m.run"]),
        ["m.run"],
    )
    execution.behaviour = None
    execution.behaviour_version = None
    execution.binding_source = TaskExecution.BindingSource.UNBOUND
    execution.save()

    enqueued: list[str] = []
    monkeypatch.setattr(
        "overbae.tasks.trace_scoring.score_trace.delay",
        lambda **kw: enqueued.append(kw["trace_id"]),
    )
    # A second parked unit of the SAME trace (different started_at) must not
    # enqueue the trace twice.
    TaskExecution.objects.create(
        project=project,
        capability=capability,
        trace_id=execution.trace_id,
        unit_span_id=uuid.uuid4().hex[:16],
        binding_source=TaskExecution.BindingSource.UNBOUND,
        observed_route={"sha": SHA, "anchors": [], "terminal": "emits_record"},
        started_at=timezone.now(),
    )
    first = behaviour_tasks.rebind_parked_executions(project_id=str(project.id))
    second = behaviour_tasks.rebind_parked_executions(project_id=str(project.id))
    assert first == {"enqueued": 1} and second == {"enqueued": 1}
    assert enqueued == [execution.trace_id] * 2
    assert TaskExecution.objects.filter(project=project).count() == 2


def test_source_qualnames_include_methods_and_nested():
    src = (
        "class Capability:\n"
        "    def run(self):\n"
        "        def inner():\n"
        "            pass\n"
        "\n"
        "def emit():\n"
        "    pass\n"
    )
    names = source_qualnames(src)
    assert {"Capability", "Capability.run", "Capability.run.<locals>.inner", "emit"} <= names


def test_verify_analysis_anchors_drops_fabricated(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "agent.py").write_text("def run():\n    pass\n")
    analysis = {
        "agents": [
            {
                "capability_card": {
                    "anchors": [
                        {
                            "qualname": "app.agent.run",
                            "kind": "entry_point",
                            "file": "app/agent.py#L1-L2",
                        },
                        {
                            "qualname": "app.agent.fabricated",
                            "kind": "tool",
                            "file": "app/agent.py#L9-L9",
                        },
                    ],
                    "trajectory_map": [
                        {"id": "p", "anchors": ["app.agent.run", "app.agent.fabricated"]}
                    ],
                }
            }
        ]
    }
    verify_analysis_anchors(analysis, str(tmp_path))
    card = analysis["agents"][0]["capability_card"]
    assert [a["qualname"] for a in card["anchors"]] == ["app.agent.run"]
    assert card["trajectory_map"][0]["anchors"] == ["app.agent.run"]


def test_task_executions_list_project_filter():
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    projects = [_project(), _project()]
    for project in projects:
        ProjectMembership.objects.create(user=user, project=project)
        TaskExecution.objects.create(
            project=project, trace_id=uuid.uuid4().hex, unit_span_id=uuid.uuid4().hex[:16]
        )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")

    assert client.get("/api/task-executions/").json()["count"] == 2
    scoped = client.get(f"/api/task-executions/?project={projects[0].id}").json()
    assert [r["id"] for r in scoped["results"]] == [str(projects[0].task_executions.get().id)]
    outside = Project.objects.create(name="X", slug=f"x-{uuid.uuid4().hex[:8]}")
    assert client.get(f"/api/task-executions/?project={outside.id}").json()["count"] == 0


def test_task_executions_list_total_tokens():
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = _project()
    ProjectMembership.objects.create(user=user, project=project)
    capability = _capability(project)
    trace_id = uuid.uuid4().hex
    root = _span(project, capability, trace_id=trace_id, qualname="app.agent.run")
    for i, tokens in enumerate((120, 80)):
        span = _span(
            project,
            capability,
            trace_id=trace_id,
            qualname="app.agent.llm",
            parent=root.span_id,
            start_ns=i + 2,
        )
        span.attributes["genai.total_tokens"] = tokens
        span.save(update_fields=["attributes"])
    TaskExecution.objects.create(project=project, trace_id=trace_id, unit_span_id=root.span_id)
    tokenless = TaskExecution.objects.create(
        project=project, trace_id=uuid.uuid4().hex, unit_span_id=uuid.uuid4().hex[:16]
    )

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    rows = {r["trace_id"]: r for r in client.get("/api/task-executions/").json()["results"]}
    assert rows[trace_id]["total_tokens"] == 200
    assert rows[tokenless.trace_id]["total_tokens"] is None
    assert rows[trace_id]["total_cost"] is None
    assert rows[trace_id]["model"] is None


def test_task_executions_list_cost_and_model():
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = _project()
    ProjectMembership.objects.create(user=user, project=project)
    capability = _capability(project)
    trace_id = uuid.uuid4().hex
    root = _span(project, capability, trace_id=trace_id, qualname="app.agent.run")
    child = _span(
        project,
        capability,
        trace_id=trace_id,
        qualname="app.agent.llm",
        parent=root.span_id,
        start_ns=2,
    )
    child.attributes["genai.total_tokens"] = 40
    child.attributes["genai.cost"] = 0.0123
    child.attributes["genai.model"] = "gpt-5-nano"
    child.save(update_fields=["attributes"])
    TaskExecution.objects.create(project=project, trace_id=trace_id, unit_span_id=root.span_id)

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    row = client.get("/api/task-executions/").json()["results"][0]
    assert row["total_tokens"] == 40
    assert row["total_cost"] == 0.0123
    assert row["model"] == "gpt-5-nano"


def test_task_executions_list_shared_trace_filters():
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = _project()
    ProjectMembership.objects.create(user=user, project=project)
    capability = _capability(project)
    hit_id = uuid.uuid4().hex
    miss_id = uuid.uuid4().hex
    now = timezone.now()
    hit_root = _span(project, capability, trace_id=hit_id, qualname="app.agent.run")
    hit_root.service_name = "worker"
    hit_root.operation = "chat"
    hit_root.span_type = "entry_point"
    hit_root.status_code = 1
    hit_root.save(update_fields=["service_name", "operation", "span_type", "status_code"])
    hit_child = _span(
        project, capability, trace_id=hit_id, qualname="app.agent.llm", parent=hit_root.span_id
    )
    hit_child.attributes["genai.total_tokens"] = 6000
    hit_child.attributes["genai.cost"] = 0.05
    hit_child.attributes["genai.model"] = "gpt-5-nano"
    hit_child.save(update_fields=["attributes"])
    miss_root = _span(project, capability, trace_id=miss_id, qualname="app.agent.run")
    TaskExecution.objects.create(
        project=project,
        capability=capability,
        conversation_id="conv-hit",
        duration_ms=8000,
        started_at=now,
        status=TaskExecution.Status.COMPLETED,
        trace_id=hit_id,
        unit_span_id=hit_root.span_id,
    )
    TaskExecution.objects.create(
        project=project,
        capability=capability,
        conversation_id="conv-miss",
        duration_ms=200,
        started_at=now - timedelta(days=10),
        status=TaskExecution.Status.ERROR,
        trace_id=miss_id,
        unit_span_id=miss_root.span_id,
    )

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")

    def ids(**params):
        return {
            r["trace_id"] for r in client.get("/api/task-executions/", params).json()["results"]
        }

    assert ids(search="conv-hit") == {hit_id}
    assert ids(has_error="true") == {miss_id}
    assert ids(has_error="false") == {hit_id}
    assert ids(min_duration_ms=5000) == {hit_id}
    assert ids(model="gpt-5-nano") == {hit_id}
    assert ids(has_model="true") == {hit_id}
    assert ids(total_tokens__gte=5000) == {hit_id}
    assert ids(total_cost__gte=0.01) == {hit_id}
    assert ids(service_name="worker") == {hit_id}
    assert ids(span_type="entry_point") == {hit_id}
    assert ids(received_at__gte=(now - timedelta(days=1)).isoformat()) == {hit_id}


def test_task_executions_list_marks_unscored_rows_pending_only_for_scoreable_agents():
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = _project()
    ProjectMembership.objects.create(user=user, project=project)

    scoreable = _capability(project)
    eval_set = EvalSet.objects.create(project=project, capability=scoreable, name="live")
    evaluator = Evaluator.objects.create(
        project=project,
        capability=scoreable,
        name="live-judge",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.TRAJECTORY,
    )
    EvalSetMember.objects.create(
        eval_set=eval_set,
        evaluator=evaluator,
        role=EvalSetMember.Role.TRACE_SCORING,
        enabled=True,
    )
    scoreable.active_eval_set = eval_set
    scoreable.save(update_fields=["active_eval_set"])

    pending = TaskExecution.objects.create(
        project=project, capability=scoreable, trace_id=uuid.uuid4().hex, unit_span_id="s1"
    )
    scored = TaskExecution.objects.create(
        project=project,
        capability=scoreable,
        trace_id=uuid.uuid4().hex,
        unit_span_id="s2",
        success_score=0.9,
    )
    errored = TaskExecution.objects.create(
        project=project,
        capability=scoreable,
        trace_id=uuid.uuid4().hex,
        unit_span_id="s3",
        status=TaskExecution.Status.ERROR,
    )
    no_evaluators = TaskExecution.objects.create(
        project=project,
        capability=_capability(project),
        trace_id=uuid.uuid4().hex,
        unit_span_id="s4",
    )
    # Pending means "no finished pass", never "no score yet".
    finished_null = TaskExecution.objects.create(
        project=project, capability=scoreable, trace_id=uuid.uuid4().hex, unit_span_id="s5"
    )
    ScoringPass.objects.create(
        project=project,
        capability=scoreable,
        trace_id=finished_null.trace_id,
        finished=timezone.now(),
    )

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    rows = {r["id"]: r for r in client.get("/api/task-executions/").json()["results"]}

    assert rows[str(pending.id)]["scoring_pending"] is True
    assert rows[str(scored.id)]["scoring_pending"] is False
    assert rows[str(errored.id)]["scoring_pending"] is False
    assert rows[str(no_evaluators.id)]["scoring_pending"] is False
    assert rows[str(finished_null.id)]["scoring_pending"] is False


def test_task_execution_detail_carries_verdict_markers():
    """The detail serializer surfaces the unit span's conflict / skipped-member
    markers so the sheet never refetches the whole trace for them."""
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = _project()
    ProjectMembership.objects.create(user=user, project=project)
    capability = _capability(project)
    trace_id = uuid.uuid4().hex
    root = _span(project, capability, trace_id=trace_id, qualname="app.agent.run")
    root.feedback_score = {
        "trace_scoring": {
            "judge_a": {"outcome": "scored", "score": 0.9},
            "_skipped_members": ["judge_b", 42],
            "_execution": {
                "score": 0.72,
                "conflict": {"lane": "output", "spread": 0.6, "members": {"a": 0.9, "b": 0.3}},
            },
        }
    }
    root.save(update_fields=["feedback_score"])
    execution = TaskExecution.objects.create(
        project=project, capability=capability, trace_id=trace_id, unit_span_id=root.span_id
    )
    bare = TaskExecution.objects.create(
        project=project, capability=capability, trace_id=uuid.uuid4().hex, unit_span_id="missing"
    )

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    row = client.get(f"/api/task-executions/{execution.id}/").json()
    assert row["execution_score"] == 0.72
    assert row["conflict"]["spread"] == 0.6
    assert row["skipped_members"] == ["judge_b"]

    empty = client.get(f"/api/task-executions/{bare.id}/").json()
    assert empty["execution_score"] is None
    assert empty["conflict"] is None
    assert empty["skipped_members"] == []


@pytest.mark.django_db
def test_conversation_turns_assembles_full_thread_payload():
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = _project()
    ProjectMembership.objects.create(user=user, project=project)
    capability = _capability(project)
    trace_id = uuid.uuid4().hex
    root = _span(
        project,
        capability,
        trace_id=trace_id,
        qualname="app.agent.run",
        extra_attrs={
            "inputs": json.dumps([{"role": "user", "content": "show me drift"}]),
            "outputs": json.dumps([{"role": "assistant", "content": "All done"}]),
        },
    )
    _span(
        project,
        capability,
        trace_id=trace_id,
        parent=root.span_id,
        start_ns=2,
        extra_attrs={
            "tool.name": "search_docs",
            "inputs": json.dumps({"query": "drift"}),
            "outputs": json.dumps({"hits": 3}),
        },
    )
    execution = TaskExecution.objects.create(
        project=project,
        capability=capability,
        trace_id=trace_id,
        unit_span_id=root.span_id,
        conversation_id="conv-1",
        user_intent={"text": "current ask", "running": "overall goal", "source": "judge"},
        observed_route={
            "anchors": ["app.agent.run"],
            "matched_anchors": ["app.agent.run"],
            "task_state": {"status": "in_progress", "outstanding_asks": ["confirm"]},
            "cluster_occupancy": {"c1": ["search_docs"]},
        },
        step_results=[
            {
                "role": "step",
                "outcome": "scored",
                "passed": True,
                "segment": ["app.agent.run"],
            },
            {"role": "outcome", "outcome": "scored", "score": 0.8},
        ],
    )

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    turns = client.get("/api/task-executions/conversation-turns/?conversation_id=conv-1").json()
    assert len(turns) == 1
    turn = turns[0]
    assert turn["id"] == str(execution.id)
    assert turn["intent"] == {"text": "overall goal", "source": "judge", "current": "current ask"}
    assert turn["input_text"] == "current ask"
    assert turn["output_text"] == "All done"
    assert turn["task_state"]["status"] == "in_progress"
    assert turn["task_state"]["outstanding_asks"] == ["confirm"]
    assert [t["name"] for t in turn["tools"]] == ["search_docs"]
    assert turn["tools"][0]["title"] == "Search docs"
    assert turn["tools"][0]["failed"] is False
    assert "drift" in turn["tools"][0]["input"]

    detail = client.get(f"/api/task-executions/{execution.id}/").json()
    flow = detail["flow"]
    assert [s["anchor"] for s in flow["steps"]] == ["app.agent.run"]
    assert flow["steps"][0]["matched"] is True
    assert flow["steps"][0]["verdict"]["passed"] is True
    assert flow["terminal"]["verdict"]["score"] == 0.8

    missing = client.get("/api/task-executions/conversation-turns/")
    assert missing.status_code == 400


@pytest.mark.django_db
def test_task_executions_group_by_conversation_paginates_whole_threads():
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = _project()
    ProjectMembership.objects.create(user=user, project=project)
    cap_a = _capability(project)
    cap_b = _capability(project)

    def _exec(*, conversation_id="", trace_id=None, capability=None, started=None):
        return TaskExecution.objects.create(
            project=project,
            capability=capability,
            conversation_id=conversation_id,
            trace_id=trace_id or uuid.uuid4().hex,
            unit_span_id=uuid.uuid4().hex[:16],
            started_at=started,
        )

    t0 = timezone.now() - timedelta(hours=1)
    # Old conversation with a NEW turn: the whole thread must surface first.
    conv_old = _exec(conversation_id="c1", started=t0)
    conv_new = _exec(conversation_id="c1", started=t0 + timedelta(minutes=50))
    # Handoff trace (two capabilities, no conversation) clusters by trace.
    handoff_trace = uuid.uuid4().hex
    hand_a = _exec(trace_id=handoff_trace, capability=cap_a, started=t0 + timedelta(minutes=30))
    hand_b = _exec(trace_id=handoff_trace, capability=cap_b, started=t0 + timedelta(minutes=10))
    # Singleton between the conversation's turns.
    solo = _exec(capability=cap_a, started=t0 + timedelta(minutes=20))

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    body = client.get(
        f"/api/task-executions/?group=conversation&project={project.id}&page_size=2"
    ).json()
    # count counts groups, and page 1 carries both full groups' rows.
    assert body["count"] == 3
    assert [row["id"] for row in body["results"]] == [
        str(conv_old.id),
        str(conv_new.id),
        str(hand_b.id),
        str(hand_a.id),
    ]

    page2 = client.get(
        f"/api/task-executions/?group=conversation&project={project.id}&page_size=2&page=2"
    ).json()
    assert [row["id"] for row in page2["results"]] == [str(solo.id)]
