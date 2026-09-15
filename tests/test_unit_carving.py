from __future__ import annotations

import uuid

import pytest

from overbae.models import (
    Behaviour,
    BehaviourVersion,
    Capability,
    EvalSet,
    EvalSetMember,
    Evaluator,
    Project,
    Span,
    TaskExecution,
    Verdict,
)
from overbae.services.eval import units as carving
from overbae.services.eval.trace_scoring import score_trace


def _span(span_id, *, parent=None, start=0, attrs=None, span_type=""):
    return Span(
        span_id=span_id,
        trace_id="t" * 32,
        parent_span_id=parent,
        start_time_ns=start,
        attributes=attrs or {},
        span_type=span_type,
    )


def _turn(span_id, *, parent, start, extra=None):
    return _span(
        span_id, parent=parent, start=start, attrs={"overmind.unit_kind": "turn", **(extra or {})}
    )


def _entry(span_id, *, parent, start):
    return _span(span_id, parent=parent, start=start, span_type="entry_point")


def test_turn_units_win_over_entry_points():
    root = _span("root", start=0, span_type="entry_point")
    t1 = _turn("t1", parent="root", start=1)
    t2 = _turn("t2", parent="root", start=2)
    carved = carving.carve([root, t1, t2])
    assert carved.multi_entry is True
    assert [(u.unit_span.span_id, u.carve_source) for u in carved.units] == [
        ("t1", carving.TURN),
        ("t2", carving.TURN),
    ]
    assert carved.turn_slices is True
    assert not any(u.degraded for u in carved.units)


def test_entry_point_with_no_turn_in_its_subtree_stays_a_unit():
    """A sibling invocation is a real unit; an entry_point enclosing turn units
    is their run boundary, not a sibling."""
    root = _span("root", start=0)
    enclosing = _entry("enclosing", parent="root", start=1)
    t1 = _turn("t1", parent="enclosing", start=2)
    legacy = _entry("legacy", parent="root", start=3)
    leaf = _span("leaf", parent="legacy", start=4)
    carved = carving.carve([root, enclosing, t1, legacy, leaf])
    assert [(u.unit_span.span_id, u.carve_source) for u in carved.units] == [
        ("t1", carving.TURN),
        ("legacy", carving.ENTRY_POINT),
    ]
    legacy_unit = carved.units[1]
    assert {s.span_id for s in legacy_unit.member_spans} == {"legacy", "leaf"}


def test_mid_trace_run_kind_span_without_turn_children_is_an_entry_point_unit():
    """A subprocess run declared mid-trace is function-typed with unit_kind="run"
    and no entry_point type; the entry-point tier must still carve it."""
    root = _span("root", start=0, span_type="entry_point")
    t1 = _turn("t1", parent="root", start=1)
    sub = _span("sub", parent="root", start=2, attrs={"overmind.unit_kind": "run"})
    leaf = _span("leaf", parent="sub", start=3)
    carved = carving.carve([root, t1, sub, leaf])
    assert [(u.unit_span.span_id, u.carve_source) for u in carved.units] == [
        ("t1", carving.TURN),
        ("sub", carving.ENTRY_POINT),
    ]
    assert {s.span_id for s in carved.units[1].member_spans} == {"sub", "leaf"}
    assert not any(u.degraded for u in carved.units)


def test_mid_trace_run_kind_span_enclosing_turns_is_their_run_boundary():
    """Same rule as an enclosing entry_point: a subprocess run boundary whose
    subtree carries turn units is those units' run boundary, not a sibling."""
    root = _span("root", start=0, span_type="entry_point")
    t1 = _turn("t1", parent="root", start=1)
    sub = _span("sub", parent="root", start=2, attrs={"overmind.unit_kind": "run"})
    t2 = _turn("t2", parent="sub", start=3)
    carved = carving.carve([root, t1, sub, t2])
    assert [(u.unit_span.span_id, u.carve_source) for u in carved.units] == [
        ("t1", carving.TURN),
        ("t2", carving.TURN),
    ]
    assert [s.span_id for s in carved.units[1].ancestor_spans] == ["root", "sub"]


def test_entry_point_typed_run_kind_span_is_one_unit():
    root = _span("root", start=0, span_type="entry_point")
    t1 = _turn("t1", parent="root", start=1)
    sub = _span(
        "sub",
        parent="root",
        start=2,
        attrs={"overmind.unit_kind": "run"},
        span_type="entry_point",
    )
    carved = carving.carve([root, t1, sub])
    assert [(u.unit_span.span_id, u.carve_source) for u in carved.units] == [
        ("t1", carving.TURN),
        ("sub", carving.ENTRY_POINT),
    ]


def test_single_declared_turn_carves_as_turn_unit_with_keyed_strays():
    """ONE declared turn under a run-boundary root is a unit — never collapsed
    to the root — and its key survives for the binder."""
    root = _span("root", start=0, span_type="entry_point")
    turn = _turn(
        "turn", parent="root", start=1, extra={"overmind.behaviour.key": "agent-step-cycle"}
    )
    step = _span("step", parent="turn", start=2)
    chain = _span("chain", parent="root", start=3)  # unkeyed instrumentor plumbing
    llm = _span(
        "llm", parent="chain", start=4, attrs={"overmind.behaviour.key": "agent-step-cycle"}
    )
    carved = carving.carve([root, turn, step, chain, llm])
    assert carved.multi_entry is True
    assert carved.turn_slices is True
    assert [(u.unit_span.span_id, u.carve_source) for u in carved.units] == [("turn", carving.TURN)]
    unit = carved.units[0]
    assert {s.span_id for s in unit.member_spans} == {"turn", "step", "llm"}
    assert unit.degraded is False
    assert [s.span_id for s in unit.ancestor_spans] == ["root"]


def test_single_entry_point_invocation_still_collapses_to_root():
    """The fewer-than-2 collapse survives for the structural tier: a lone
    entry_point invocation genuinely is the whole run."""
    root = _span("root", start=0)
    ep = _entry("ep", parent="root", start=1)
    leaf = _span("leaf", parent="ep", start=2)
    carved = carving.carve([root, ep, leaf])
    assert carved.multi_entry is False
    assert carved.units[0].unit_span.span_id == "root"
    assert carved.units[0].carve_source == carving.ROOT


def test_rootless_single_turn_is_an_interrupted_turn_unit():
    head = _span("head", parent="gone", start=1)
    turn = _turn("turn", parent="head", start=2)
    carved = carving.carve([head, turn])
    assert carved.rootless is True
    assert [(u.unit_span.span_id, u.carve_source, u.interrupted) for u in carved.units] == [
        ("turn", carving.TURN, True)
    ]


def test_entry_point_fallback_when_no_turn_spans():
    root = _span("root", start=0)
    e1 = _entry("e1", parent="root", start=1)
    e2 = _entry("e2", parent="root", start=2)
    carved = carving.carve([root, e1, e2])
    assert [u.carve_source for u in carved.units] == [carving.ENTRY_POINT, carving.ENTRY_POINT]
    assert carved.turn_slices is False


def test_keyed_stray_outside_every_subtree_joins_its_turn_unit():
    """Callback instrumentors parent LLM/tool spans outside the turn subtree; the
    ambient key stamp decides membership."""
    root = _span("root", start=0, span_type="entry_point")
    t1 = _turn("t1", parent="root", start=1, extra={"overmind.behaviour.key": "analyst"})
    t2 = _turn("t2", parent="root", start=5, extra={"overmind.behaviour.key": "manager"})
    chain = _span("chain", parent="root", start=2)  # unkeyed instrumentor plumbing
    llm = _span("llm", parent="chain", start=3, attrs={"overmind.behaviour.key": "analyst"})
    tool = _span("tool", parent="chain", start=4, attrs={"overmind.behaviour.key": "analyst"})
    carved = carving.carve([root, t1, t2, chain, llm, tool])
    analyst = next(u for u in carved.units if u.unit_span.span_id == "t1")
    assert {s.span_id for s in analyst.member_spans} == {"t1", "llm", "tool"}
    manager = next(u for u in carved.units if u.unit_span.span_id == "t2")
    assert {s.span_id for s in manager.member_spans} == {"t2"}
    assert not any(u.degraded for u in carved.units)


def test_keyed_span_inside_another_units_subtree_is_not_reattached():
    root = _span("root", start=0, span_type="entry_point")
    t1 = _turn("t1", parent="root", start=1, extra={"overmind.behaviour.key": "analyst"})
    t2 = _turn("t2", parent="root", start=2, extra={"overmind.behaviour.key": "manager"})
    # Mislabelled but structurally interior to t2 — the subtree wins.
    inner = _span("inner", parent="t2", start=3, attrs={"overmind.behaviour.key": "analyst"})
    carved = carving.carve([root, t1, t2, inner])
    analyst = next(u for u in carved.units if u.unit_span.span_id == "t1")
    assert {s.span_id for s in analyst.member_spans} == {"t1"}
    manager = next(u for u in carved.units if u.unit_span.span_id == "t2")
    assert {s.span_id for s in manager.member_spans} == {"t2", "inner"}


def test_stray_with_unmatched_key_and_dropped_boundary_stay_out():
    root = _span("root", start=0, span_type="entry_point")
    t1 = _turn("t1", parent="root", start=1, extra={"overmind.behaviour.key": "analyst"})
    t2 = _turn("t2", parent="root", start=2, extra={"overmind.behaviour.key": "manager"})
    unmatched = _span(
        "unmatched", parent="root", start=3, attrs={"overmind.behaviour.key": "elsewhere"}
    )
    # An enclosing entry_point dropped as a run boundary never becomes
    # interior evidence, even mislabelled with a unit's key.
    boundary = _span(
        "boundary",
        parent="root",
        start=4,
        span_type="entry_point",
        attrs={"overmind.behaviour.key": "analyst"},
    )
    t3 = _turn("t3", parent="boundary", start=5)
    carved = carving.carve([root, t1, t2, unmatched, boundary, t3])
    for u in carved.units:
        member_ids = {s.span_id for s in u.member_spans}
        assert "unmatched" not in member_ids
        assert "boundary" not in member_ids


def test_reentrant_key_stray_attaches_to_latest_preceding_turn():
    root = _span("root", start=0, span_type="entry_point")
    first = _turn("first", parent="root", start=1, extra={"overmind.behaviour.key": "analyst"})
    second = _turn("second", parent="root", start=4, extra={"overmind.behaviour.key": "analyst"})
    stray = _span("stray", parent="root", start=5, attrs={"overmind.behaviour.key": "analyst"})
    early = _span("early", parent="root", start=2, attrs={"overmind.behaviour.key": "analyst"})
    carved = carving.carve([root, first, second, stray, early])
    by_unit = {u.unit_span.span_id: {s.span_id for s in u.member_spans} for u in carved.units}
    assert by_unit["first"] == {"first", "early"}
    assert by_unit["second"] == {"second", "stray"}


def test_key_segment_shim_carves_flat_keyed_trace():
    root = _span("root", start=0, span_type="entry_point")
    a1 = _span("a1", parent="root", start=1, attrs={"overmind.behaviour.key": "analyst"})
    a2 = _span("a2", parent="root", start=2, attrs={"overmind.behaviour.key": "analyst"})
    b1 = _span("b1", parent="root", start=3, attrs={"overmind.behaviour.key": "manager"})
    carved = carving.carve([root, a1, a2, b1])
    assert carved.multi_entry is True
    assert carved.turn_slices is True
    assert [(u.unit_span.span_id, u.carve_source, u.degraded) for u in carved.units] == [
        ("a1", carving.KEY_SEGMENT, True),
        ("b1", carving.KEY_SEGMENT, True),
    ]
    assert {s.span_id for s in carved.units[0].member_spans} == {"a1", "a2"}


def test_key_segment_shim_needs_two_distinct_keys():
    """A single key group is no segmentation evidence: keyed children with no
    turn stamp collapse to the root, they never carve through the shim."""
    root = _span("root", start=0)
    a1 = _span("a1", parent="root", start=1, attrs={"overmind.behaviour.key": "analyst"})
    a2 = _span("a2", parent="root", start=2, attrs={"overmind.behaviour.key": "analyst"})
    carved = carving.carve([root, a1, a2])
    assert carved.multi_entry is False
    assert carved.units[0].unit_span.span_id == "root"
    assert carved.units[0].carve_source == carving.ROOT


def test_unkeyed_delivery_span_joins_the_enclosing_key_group():
    """An unkeyed deliver() span attaches to the key group most recently started
    before it, so declared delivery still names the terminal unit."""
    root = _span("root", start=0)
    a1 = _span("a1", parent="root", start=1, attrs={"overmind.behaviour.key": "analyst"})
    b1 = _span("b1", parent="root", start=3, attrs={"overmind.behaviour.key": "manager"})
    deliver = _span("deliver", parent="root", start=4, attrs={"overmind.delivery": "true"})
    carved = carving.carve([root, a1, b1, deliver])
    manager_unit = next(u for u in carved.units if u.unit_span.span_id == "b1")
    assert {s.span_id for s in manager_unit.member_spans} == {"b1", "deliver"}
    analyst_unit = next(u for u in carved.units if u.unit_span.span_id == "a1")
    assert {s.span_id for s in analyst_unit.member_spans} == {"a1"}


def test_root_fallback_is_degraded_only_without_a_declared_boundary():
    plain_root = _span("root", start=0)
    leaf = _span("leaf", parent="root", start=1)
    carved = carving.carve([plain_root, leaf])
    assert carved.units[0].carve_source == carving.ROOT
    assert carved.units[0].degraded is True
    assert {s.span_id for s in carved.units[0].member_spans} == {"root", "leaf"}

    declared_root = _span("root", start=0, span_type="entry_point")
    carved = carving.carve([declared_root, _span("leaf", parent="root", start=1)])
    assert carved.units[0].carve_source == carving.ROOT
    assert carved.units[0].degraded is False


def test_rootless_trace_carves_earliest_span_as_interrupted_head():
    only = _span("s1", parent="gone", start=1)
    child = _span("s2", parent="s1", start=2)
    carved = carving.carve([only, child])
    assert carved.rootless is True
    assert carved.root.span_id == "s1"
    assert carved.units[0].interrupted is True


def test_rootless_multi_entry_marks_only_the_last_unit_interrupted():
    """Earlier units genuinely completed; the last-starting unit is the nearest
    proxy for the work in flight when the run died."""
    t1 = _turn("t1", parent="gone", start=1)
    t2 = _turn("t2", parent="gone", start=2)
    t3 = _turn("t3", parent="gone", start=3)
    carved = carving.carve([t1, t2, t3])
    assert carved.rootless is True
    # The fallback head absorbs the earliest turn; the rest are the units.
    assert [(u.unit_span.span_id, u.interrupted) for u in carved.units] == [
        ("t2", False),
        ("t3", True),
    ]


def test_run_surfaces_offer_the_root_over_its_whole_subtree():
    root = _span("root", start=0, span_type="entry_point")
    t1 = _turn("t1", parent="root", start=1)
    t2 = _turn("t2", parent="root", start=2)
    leaf = _span("leaf", parent="t1", start=3)
    spans = [root, t1, t2, leaf]
    carved = carving.carve(spans)
    (surface,) = carving.run_surfaces(spans, carved)
    assert surface.unit_span.span_id == "root"
    assert surface.carve_source == carving.RUN_SURFACE
    assert {s.span_id for s in surface.member_spans} == {"root", "t1", "t2", "leaf"}
    assert surface.degraded is False


def test_run_surfaces_include_a_dropped_mid_trace_run_boundary():
    """A subprocess run boundary demoted to its turns' run boundary is a
    run-grain surface over its own subtree, beside the root's."""
    root = _span("root", start=0, span_type="entry_point")
    t1 = _turn("t1", parent="root", start=1)
    sub = _span("sub", parent="root", start=2, attrs={"overmind.unit_kind": "run"})
    t2 = _turn("t2", parent="sub", start=3)
    spans = [root, t1, sub, t2]
    carved = carving.carve(spans)
    surfaces = {u.unit_span.span_id: u for u in carving.run_surfaces(spans, carved)}
    assert set(surfaces) == {"root", "sub"}
    assert {s.span_id for s in surfaces["sub"].member_spans} == {"sub", "t2"}


def test_run_surfaces_absent_without_turn_slices_or_root():
    # Sibling invocations: each entry_point is a complete run already.
    root = _span("root", start=0)
    e1 = _entry("e1", parent="root", start=1)
    e2 = _entry("e2", parent="root", start=2)
    spans = [root, e1, e2]
    assert carving.run_surfaces(spans, carving.carve(spans)) == []

    # Root collapse: the root already scores the whole trace.
    solo = [_span("root", start=0, span_type="entry_point"), _span("leaf", parent="root", start=1)]
    assert carving.run_surfaces(solo, carving.carve(solo)) == []

    # Rootless: the run boundary never arrived, so there is nothing to offer.
    orphaned = [_turn("t1", parent="gone", start=1), _turn("t2", parent="gone", start=2)]
    assert carving.run_surfaces(orphaned, carving.carve(orphaned)) == []


pytestmark_db = pytest.mark.django_db


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _agent(project, name="A") -> Capability:
    capability = Capability.objects.create(
        project=project, name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:6]}"
    )
    eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
    capability.active_eval_set = eval_set
    capability.save(update_fields=["active_eval_set"])
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="contains-Paris",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.TURN,
        version=1,
        pass_threshold=1.0,
        config={"check": "contains", "reference": "Paris"},
    )
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=evaluator, role=EvalSetMember.Role.TRACE_SCORING
    )
    return capability


def _db_span(project, capability, *, trace_id, parent, start, attrs, span_type=""):
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=parent,
        project=project,
        capability=capability,
        span_type=span_type,
        status_code=1,
        start_time_ns=start,
        attributes=attrs,
    )


@pytestmark_db
def test_mixed_turn_and_entry_point_trace_scores_both_units():
    project = _project()
    capability = _agent(project)
    trace_id = uuid.uuid4().hex
    root = _db_span(
        project,
        capability,
        trace_id=trace_id,
        parent=None,
        start=0,
        attrs={"overmind.span.type": "entry_point", "overmind.unit_kind": "run"},
        span_type="entry_point",
    )
    turn = _db_span(
        project,
        capability,
        trace_id=trace_id,
        parent=root.span_id,
        start=1,
        attrs={
            "overmind.unit_kind": "turn",
            "overmind.input.data": "q",
            "overmind.output.data": "Paris turn",
            "outputs": "Paris turn",
        },
    )
    legacy = _db_span(
        project,
        capability,
        trace_id=trace_id,
        parent=root.span_id,
        start=2,
        attrs={
            "overmind.span.type": "entry_point",
            "overmind.input.data": "q",
            "overmind.output.data": "Paris legacy",
            "outputs": "Paris legacy",
        },
        span_type="entry_point",
    )

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["mode"] == "multi_entry"
    assert result["invocations"] == 2

    executions = {
        e.unit_span_id: e for e in TaskExecution.objects.filter(project=project, trace_id=trace_id)
    }
    assert set(executions) == {turn.span_id, legacy.span_id}
    assert executions[turn.span_id].observed_route["carve_source"] == "turn"
    assert executions[legacy.span_id].observed_route["carve_source"] == "entry_point"
    assert all("degraded_carve" not in e.route_flags for e in executions.values())


@pytestmark_db
def test_single_turn_trace_scores_the_turn_unit_not_the_root():
    """The execution lands on the turn span with ``carve_source="turn"``; the
    root gets only the invocations summary."""
    project = _project()
    capability = _agent(project)
    trace_id = uuid.uuid4().hex
    root = _db_span(
        project,
        capability,
        trace_id=trace_id,
        parent=None,
        start=0,
        attrs={"overmind.span.type": "entry_point", "overmind.unit_kind": "run"},
        span_type="entry_point",
    )
    turn = _db_span(
        project,
        capability,
        trace_id=trace_id,
        parent=root.span_id,
        start=1,
        attrs={
            "overmind.unit_kind": "turn",
            "overmind.behaviour.key": "agent-step-cycle",
            "overmind.input.data": "q",
            "overmind.output.data": "Paris turn",
            "outputs": "Paris turn",
        },
    )
    stray = _db_span(
        project,
        capability,
        trace_id=trace_id,
        parent=root.span_id,
        start=2,
        attrs={
            "overmind.behaviour.key": "agent-step-cycle",
            "code.namespace": "app.agent",
            "code.function.name": "call_llm",
        },
    )

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["mode"] == "multi_entry"
    assert result["invocations"] == 1
    assert result["invocations_ok"] == 1

    executions = list(TaskExecution.objects.filter(project=project, trace_id=trace_id))
    assert [e.unit_span_id for e in executions] == [turn.span_id]
    route = executions[0].observed_route
    assert route["carve_source"] == "turn"
    assert "degraded_carve" not in executions[0].route_flags
    # The keyed stray joined the turn unit: its qualname is route evidence.
    assert "app.agent.call_llm" in route["anchors"]
    assert stray.span_id != turn.span_id

    turn_verdict = Verdict.objects.get(
        project=project, target_id=turn.span_id, evaluator_name="contains-Paris"
    )
    assert turn_verdict.metadata["passed"] is True
    root.refresh_from_db()
    root_block = (root.feedback_score or {}).get("trace_scoring") or {}
    assert root_block["invocations"]["rationale"] == "1/1 invocations passed"
    assert not Verdict.objects.filter(
        project=project, target_id=root.span_id, evaluator_name="contains-Paris"
    ).exists()


@pytestmark_db
def test_rootless_multi_entry_interrupts_the_unit_in_flight():
    project = _project()
    capability = _agent(project)
    trace_id = uuid.uuid4().hex
    missing_root = uuid.uuid4().hex[:16]
    turns = [
        _db_span(
            project,
            capability,
            trace_id=trace_id,
            parent=missing_root,
            start=i + 1,
            attrs={
                "overmind.unit_kind": "turn",
                "overmind.input.data": "q",
                "overmind.output.data": f"Paris {i}",
                "outputs": f"Paris {i}",
            },
        )
        for i in range(3)
    ]

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["mode"] == "multi_entry"

    executions = {
        e.unit_span_id: e for e in TaskExecution.objects.filter(project=project, trace_id=trace_id)
    }
    # The earliest turn is the fallback head (structural root), not a unit.
    assert set(executions) == {turns[1].span_id, turns[2].span_id}
    assert executions[turns[1].span_id].status == TaskExecution.Status.COMPLETED
    assert executions[turns[2].span_id].status == TaskExecution.Status.INTERRUPTED
    assert executions[turns[2].span_id].terminal_kind == "interrupted"


def _behaviour(capability, key, entry, sequence, *, grain=Behaviour.Grain.TURN, claim="code_path"):
    # Grain is a scanned column; fixtures write it the way the scan does.
    behaviour = Behaviour.objects.create(
        project=capability.project,
        capability=capability,
        key=key,
        display_name=key,
        entry_anchor=entry,
        grain=grain,
    )
    BehaviourVersion.objects.create(
        behaviour=behaviour,
        analyzed_sha="a" * 40,
        contract={
            "key": key,
            "entry_anchor": entry,
            "claim": claim,
            "anchor_sequence": sequence,
            "anchors": [
                {"qualname": q, "kind": "function", "file": "app/agent.py#L1-L10"} for q in sequence
            ],
            "terminal": {"kind": "emits_record", "description": ""},
        },
    )
    return behaviour


def _bound_outcome_member(capability, behaviour_key, name):
    evaluator = Evaluator.objects.create(
        project=capability.project,
        capability=capability,
        name=name,
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.TRAJECTORY,
        version=1,
        pass_threshold=1.0,
        config={
            "check": "contains",
            "reference": "Paris",
            "behaviour": {"behaviour_key": behaviour_key, "role": "outcome", "anchor_segment": []},
        },
    )
    return EvalSetMember.objects.create(
        eval_set=capability.active_eval_set,
        evaluator=evaluator,
        role=EvalSetMember.Role.TRACE_SCORING,
        order=1,
    )


def _code_attrs(qualname, extra=None):
    ns, _, fn = qualname.rpartition(".")
    return {"code.namespace": ns, "code.function.name": fn, **(extra or {})}


@pytestmark_db
def test_run_grain_behaviour_binds_the_run_surface_beside_the_declared_turn():
    """The turn binds its declared behaviour; the root additionally materializes
    as a run-grain surface bound by anchor join, carrying that behaviour's
    judges — and only those."""
    project = _project()
    capability = _agent(project)
    _behaviour(
        capability,
        "run-startup",
        "deerflow.runtime.runs.worker.run_agent",
        [
            "deerflow.runtime.runs.worker.run_agent",
            "deerflow.agents.lead_agent.agent._assemble_lead_agent",
        ],
    )
    loop = _behaviour(
        capability,
        "lead-tool-loop",
        "deerflow.runtime.runs.worker.run_agent",
        [
            "deerflow.runtime.runs.worker.run_agent",
            "deerflow.middlewares.summarization.DeerFlowSummarizationMiddleware.abefore_model",
            "deerflow.middlewares.title.TitleMiddleware.aafter_model",
        ],
        grain=Behaviour.Grain.RUN,
        claim="decision_surface",
    )
    _bound_outcome_member(capability, "lead-tool-loop", "lead-tool-loop-success")

    trace_id = uuid.uuid4().hex
    root = _db_span(
        project,
        capability,
        trace_id=trace_id,
        parent=None,
        start=0,
        attrs={
            "overmind.span.type": "entry_point",
            "overmind.unit_kind": "run",
            "overmind.output.data": "Paris final",
            "outputs": "Paris final",
        },
        span_type="entry_point",
    )
    run_agent = _db_span(
        project,
        capability,
        trace_id=trace_id,
        parent=root.span_id,
        start=1,
        attrs=_code_attrs("deerflow.runtime.runs.worker.run_agent"),
        span_type="function",
    )
    turn = _db_span(
        project,
        capability,
        trace_id=trace_id,
        parent=run_agent.span_id,
        start=2,
        attrs={
            "overmind.unit_kind": "turn",
            "overmind.behaviour.key": "run-startup",
            "overmind.input.data": "q",
            "overmind.output.data": "Paris turn",
            "outputs": "Paris turn",
        },
    )
    for i, qualname in enumerate(
        [
            "deerflow.agents.lead_agent.agent._assemble_lead_agent",
            "deerflow.middlewares.summarization.DeerFlowSummarizationMiddleware.abefore_model",
            "deerflow.middlewares.title.TitleMiddleware.aafter_model",
        ]
    ):
        _db_span(
            project,
            capability,
            trace_id=trace_id,
            parent=turn.span_id,
            start=i + 3,
            attrs=_code_attrs(qualname),
            span_type="function",
        )

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["mode"] == "multi_entry"
    # The run surface is not an invocation; the declared turn is the only one.
    assert result["invocations"] == 1

    executions = {
        e.unit_span_id: e for e in TaskExecution.objects.filter(project=project, trace_id=trace_id)
    }
    assert set(executions) == {turn.span_id, root.span_id}
    turn_execution = executions[turn.span_id]
    assert turn_execution.behaviour.key == "run-startup"
    assert turn_execution.binding_source == TaskExecution.BindingSource.DECLARED
    assert turn_execution.observed_route["carve_source"] == "turn"
    run_execution = executions[root.span_id]
    assert run_execution.behaviour_id == loop.id
    assert run_execution.binding_source == TaskExecution.BindingSource.ANCHOR_JOIN
    assert run_execution.observed_route["carve_source"] == "run"
    assert "degraded_carve" not in run_execution.route_flags

    # The run surface carries the bound behaviour's judges — and only those;
    # the turn unit keeps the generic suite and skips the loop's judge.
    root.refresh_from_db()
    root_block = (root.feedback_score or {}).get("trace_scoring") or {}
    root_loop = Verdict.objects.get(
        project=project, target_id=root.span_id, evaluator_name="lead-tool-loop-success"
    )
    assert root_loop.metadata["passed"] is True
    assert not Verdict.objects.filter(
        project=project, target_id=root.span_id, evaluator_name="contains-Paris"
    ).exists()
    assert root_block["invocations"]["rationale"] == "1/1 invocations passed"
    turn.refresh_from_db()
    turn_block = (turn.feedback_score or {}).get("trace_scoring") or {}
    turn_verdict = Verdict.objects.get(
        project=project, target_id=turn.span_id, evaluator_name="contains-Paris"
    )
    assert turn_verdict.metadata["passed"] is True
    assert not Verdict.objects.filter(
        project=project,
        target_id=turn.span_id,
        evaluator_name="lead-tool-loop-success",
        outcome=Verdict.Outcome.SCORED,
    ).exists()
    assert "lead-tool-loop-success" in turn_block["_skipped_members"]

    # Idempotent: a re-run keeps both rows and the root's verdicts + summary.
    assert score_trace(trace_id, str(project.id))["status"] == "no_change"
    assert TaskExecution.objects.filter(project=project, trace_id=trace_id).count() == 2
    root.refresh_from_db()
    root_block = (root.feedback_score or {}).get("trace_scoring") or {}
    assert Verdict.objects.filter(
        project=project, target_id=root.span_id, evaluator_name="lead-tool-loop-success"
    ).exists()
    assert "invocations" in root_block


@pytestmark_db
def test_turn_sliced_trace_without_run_grain_behaviour_grows_no_root_execution():
    project = _project()
    capability = _agent(project)
    _behaviour(capability, "run-startup", "app.agent.run", ["app.agent.run"])
    trace_id = uuid.uuid4().hex
    root = _db_span(
        project,
        capability,
        trace_id=trace_id,
        parent=None,
        start=0,
        attrs={"overmind.span.type": "entry_point", "overmind.unit_kind": "run"},
        span_type="entry_point",
    )
    turn = _db_span(
        project,
        capability,
        trace_id=trace_id,
        parent=root.span_id,
        start=1,
        attrs={
            "overmind.unit_kind": "turn",
            "overmind.behaviour.key": "run-startup",
            "overmind.output.data": "Paris turn",
            "outputs": "Paris turn",
        },
    )

    assert score_trace(trace_id, str(project.id))["status"] == "scored"
    unit_span_ids = set(
        TaskExecution.objects.filter(project=project, trace_id=trace_id).values_list(
            "unit_span_id", flat=True
        )
    )
    assert unit_span_ids == {turn.span_id}


@pytestmark_db
def test_orphan_function_fragment_skips_and_voids_prior_execution():
    """A boundary-less trace of one interior function is not a run: no execution
    mints, and a row a prior pass minted is voided on re-score."""
    project = _project()
    capability = _agent(project)
    _behaviour(
        capability,
        "run-startup",
        "deerflow.runtime.runs.worker.run_agent",
        [
            "deerflow.runtime.runs.worker.run_agent",
            "deerflow.agents.lead_agent.agent._assemble_lead_agent",
        ],
    )
    trace_id = uuid.uuid4().hex
    fragment = _db_span(
        project,
        capability,
        trace_id=trace_id,
        parent=None,
        start=0,
        attrs=_code_attrs("deerflow.agents.lead_agent.agent._assemble_lead_agent"),
        span_type="function",
    )
    TaskExecution.objects.create(
        project=project, capability=capability, trace_id=trace_id, unit_span_id=fragment.span_id
    )

    result = score_trace(trace_id, str(project.id))
    assert result == {"status": "skipped", "reason": "orphan_fragment", "trace_id": trace_id}
    assert not TaskExecution.objects.filter(project=project, trace_id=trace_id).exists()


@pytestmark_db
def test_single_declared_boundary_span_stays_scorable():
    """A deliberate one-shot invocation is the run: a lone entry_point span
    scores at the root tier, undegraded."""
    project = _project()
    capability = _agent(project)
    trace_id = uuid.uuid4().hex
    root = _db_span(
        project,
        capability,
        trace_id=trace_id,
        parent=None,
        start=0,
        attrs={
            "overmind.span.type": "entry_point",
            "overmind.input.data": "q",
            "overmind.output.data": "Paris",
            "outputs": "Paris",
        },
        span_type="entry_point",
    )

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    (execution,) = TaskExecution.objects.filter(project=project, trace_id=trace_id)
    assert execution.unit_span_id == root.span_id
    assert execution.observed_route["carve_source"] == "root"
    assert "degraded_carve" not in execution.route_flags


@pytestmark_db
def test_key_segment_executions_carry_carve_source_and_degraded_flag():
    project = _project()
    capability = _agent(project)
    trace_id = uuid.uuid4().hex
    root = _db_span(
        project,
        capability,
        trace_id=trace_id,
        parent=None,
        start=0,
        attrs={"overmind.span.type": "entry_point", "overmind.unit_kind": "run"},
        span_type="entry_point",
    )
    for i, key in enumerate(["analyst", "manager"]):
        _db_span(
            project,
            capability,
            trace_id=trace_id,
            parent=root.span_id,
            start=i + 1,
            attrs={
                "overmind.behaviour.key": key,
                "overmind.input.data": "q",
                "overmind.output.data": f"Paris {key}",
                "outputs": f"Paris {key}",
            },
        )

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["invocations"] == 2

    for execution in TaskExecution.objects.filter(project=project, trace_id=trace_id):
        assert execution.observed_route["carve_source"] == "key_segment"
        assert "degraded_carve" in execution.route_flags
