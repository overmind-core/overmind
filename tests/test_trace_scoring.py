from __future__ import annotations

import uuid

import pytest

from overbae.api.otlp import _resolve_capability
from overbae.models import (
    Capability,
    EvalSet,
    EvalSetMember,
    Evaluator,
    Span,
    TaskExecution,
    Verdict,
)
from overbae.services.eval.composition import compose
from overbae.services.eval.trace_scoring import FEEDBACK_KEY, execution_score, score_trace
from tests.factories import make_behaviour, make_capability, make_project

pytestmark = pytest.mark.django_db


def _contains_member(
    capability,
    needle="Paris",
    role=EvalSetMember.Role.TRACE_SCORING,
    scope=Evaluator.Scope.TRAJECTORY,
    order=0,
):
    evaluator = Evaluator.objects.create(
        project=capability.project,
        capability=capability,
        name=f"contains-{needle}",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=scope,
        version=1,
        pass_threshold=1.0,
        config={"check": "contains", "reference": needle},
    )
    return EvalSetMember.objects.create(
        eval_set=capability.active_eval_set, evaluator=evaluator, role=role, order=order
    )


def _verdict(project, span, evaluator_name) -> Verdict:
    return Verdict.objects.get(
        project=project, target_id=span.span_id, evaluator_name=evaluator_name
    )


def _span(
    project, capability, *, trace_id, connector=False, output="The capital of France is Paris."
):
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=None,
        project=project,
        capability=capability,
        resource_attrs={"connector.source": "langfuse"} if connector else {},
        attributes={
            "overmind.input.data": "What is the capital of France?",
            "overmind.output.data": output,
        },
    )


def test_native_trace_gets_scored():
    project = make_project()
    capability = make_capability(project, with_set=True)
    member = _contains_member(capability)
    trace_id = uuid.uuid4().hex
    span = _span(project, capability, trace_id=trace_id)

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["scored"] == 1

    span.refresh_from_db()
    block = (span.feedback_score or {}).get(FEEDBACK_KEY) or {}
    assert block["_execution"]["score"] == 1.0
    verdict = _verdict(project, span, member.evaluator.name)
    assert verdict.score == 1.0
    assert verdict.metadata["passed"] is True
    assert verdict.evaluator_id == member.evaluator_id


def test_scoring_is_idempotent():
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability)
    trace_id = uuid.uuid4().hex
    span = _span(project, capability, trace_id=trace_id)

    first = score_trace(trace_id, str(project.id))
    assert first["status"] == "scored"
    span.refresh_from_db()
    first_block = dict((span.feedback_score or {})[FEEDBACK_KEY])

    second = score_trace(trace_id, str(project.id))
    assert second["status"] == "no_change"
    span.refresh_from_db()
    assert set((span.feedback_score or {})[FEEDBACK_KEY]) == set(first_block)


def test_noop_when_no_capability():
    project = make_project()
    trace_id = uuid.uuid4().hex
    _span(project, None, trace_id=trace_id)
    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "skipped"
    assert result["reason"] == "no_capability"


def test_tool_vocabulary_member_does_not_run_on_live_traces():
    project = make_project()
    capability = make_capability(project, with_set=True)
    gate = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="tool-vocabulary-selection",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.TRAJECTORY,
        version=1,
        config={"check": "tool_selection", "expected_tools": ["search"]},
    )
    EvalSetMember.objects.create(
        eval_set=capability.active_eval_set,
        evaluator=gate,
        role=EvalSetMember.Role.TRACE_SCORING,
        order=0,
    )
    _contains_member(capability, order=1)
    trace_id = uuid.uuid4().hex
    span = _span(project, capability, trace_id=trace_id)
    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    span.refresh_from_db()
    names = set(
        Verdict.objects.filter(project=project, target_id=span.span_id).values_list(
            "evaluator_name", flat=True
        )
    )
    assert "tool-vocabulary-selection" not in names
    assert "contains-Paris" in names


def test_model_surface_member_does_not_gate_live_trace_scoring():
    """A stale TRACE_SCORING membership on a model-surface grader (extraction
    JSON schema) must not run — or short-circuit — live harness deliverables."""
    project = make_project()
    capability = make_capability(project, with_set=True)
    gate = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="output-schema-field-conformance",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.TRAJECTORY,
        surface=Evaluator.Surface.MODEL,
        version=1,
        config={"check": "schema_field_conformance", "fields": {"x": {"type": "number"}}},
    )
    EvalSetMember.objects.create(
        eval_set=capability.active_eval_set,
        evaluator=gate,
        role=EvalSetMember.Role.TRACE_SCORING,
        order=0,
    )
    _contains_member(capability, order=1)
    trace_id = uuid.uuid4().hex
    span = _span(project, capability, trace_id=trace_id, output="The capital of France is Paris.")
    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    span.refresh_from_db()
    names = set(
        Verdict.objects.filter(project=project, target_id=span.span_id).values_list(
            "evaluator_name", flat=True
        )
    )
    assert "output-schema-field-conformance" not in names
    assert "contains-Paris" in names


def test_noop_when_no_trace_scoring_members():
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability, role=EvalSetMember.Role.GENERATIVE)
    trace_id = uuid.uuid4().hex
    span = _span(project, capability, trace_id=trace_id)

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "skipped"
    assert result["reason"] == "no_members"
    span.refresh_from_db()
    assert (span.feedback_score or {}).get(FEEDBACK_KEY) is None


def test_noop_when_no_active_eval_set():
    project = make_project()
    capability = make_capability(project)
    trace_id = uuid.uuid4().hex
    _span(project, capability, trace_id=trace_id)
    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "skipped"
    assert result["reason"] == "no_eval_set"


def test_connector_trace_is_scored():
    project = make_project()
    capability = make_capability(project, with_set=True)
    member = _contains_member(capability)
    trace_id = uuid.uuid4().hex
    span = _span(project, capability, trace_id=trace_id, connector=True)

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["scored"] == 1

    span.refresh_from_db()
    assert FEEDBACK_KEY in (span.feedback_score or {})
    assert _verdict(project, span, member.evaluator.name).metadata["passed"] is True


def test_error_trace_is_skipped():
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability)
    trace_id = uuid.uuid4().hex
    span = _span(project, capability, trace_id=trace_id)
    Span.objects.filter(pk=span.pk).update(status_code=2)

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "skipped"
    assert result["reason"] == "error_trace"
    span.refresh_from_db()
    assert (span.feedback_score or {}).get(FEEDBACK_KEY) is None


def test_ingest_resolves_scan_capability_by_id():
    project = make_project()
    scan_agent = Capability.objects.create(
        project=project,
        name="Ledgerline Invoice Triage Capability",
        slug="ledgerline-invoice-triage",
    )
    eval_set = EvalSet.objects.create(project=project, capability=scan_agent, name="Default")
    scan_agent.active_eval_set = eval_set
    scan_agent.save(update_fields=["active_eval_set"])

    resolved = _resolve_capability(project, {}, {"overmind.capability.id": str(scan_agent.id)})
    assert resolved is not None
    assert resolved.id == scan_agent.id
    assert Capability.objects.filter(project=project).count() == 1


def test_ingest_never_creates_a_capability():
    project = make_project()
    resolved = _resolve_capability(project, {}, {"overmind.capability.id": str(uuid.uuid4())})
    assert resolved is None
    assert Capability.objects.filter(project=project).count() == 0


def _entry(score, *, scope="final_output", passed=None, **extra):
    return {"score": score, "passed": passed, "outcome": "scored", "scope": scope, **extra}


def test_execution_score_reads_persisted_marker_only():
    """No compose-on-the-fly fallback: the ``_execution`` marker is the only
    score copy on the span (migration 0139 backfilled pre-marker rows)."""
    assert execution_score({FEEDBACK_KEY: {"_execution": {"score": 0.9}}}) == 0.9
    assert execution_score({FEEDBACK_KEY: {"out-eval": _entry(0.0)}}) is None
    assert execution_score({}) is None
    assert execution_score(None) is None


def test_compose_redistributes_weight_of_na_and_empty_phases():
    block = {
        "traj-eval": _entry(1.0, scope="trajectory"),
        "out-eval": _entry(0.5),
        "na-step": {"score": None, "outcome": "not_applicable", "scope": "step"},
    }
    result = compose(block)
    assert result["phases"]["steps"]["n"] == 0
    assert result["phases"]["steps"]["na"] == 1
    assert result["not_applicable"] == 1
    # The empty steps phase's 0.25 weight redistributes to the other two.
    assert result["score"] == round((0.25 * 1.0 + 0.5 * 0.5) / 0.75, 4)


def test_compose_failed_gate_verdict_never_zeroes_the_composite():
    """No gated evals: a failed conformance/verification verdict is a 0.0 in
    its own phase, never a wipe of every other member's signal."""
    block = {
        "gate-eval": _entry(0.0, passed=False, gate=True),
        "good-eval": _entry(1.0),
    }
    result = compose(block)
    assert "gated" not in result
    # Both are output-phase entries: mean of 0.0 and 1.0.
    assert result["score"] == 0.5


def test_compose_runtime_gate_marker_does_not_zero_the_composite():
    block = {"judge": _entry(1.0, sub_scores=[{"_runtime_gate": {"gated_fail": True}}])}
    result = compose(block)
    assert result["score"] == 1.0


def test_compose_failed_boolean_dominates_passing_defers_to_scalar():
    assert compose({"bool-eval": _entry(0.7, passed=True)})["score"] == 0.7
    assert compose({"bool-eval": _entry(0.7, passed=False)})["score"] == 0.0
    assert compose({"judge": _entry(0.0, passed=True)})["score"] == 0.0
    entry = _entry(None, passed=True)
    entry.pop("score", None)
    assert compose({"bool-eval": entry})["score"] == 1.0


def test_compose_checkpoint_sub_scores_route_to_steps_phase():
    block = {
        "checkpoint-coverage": _entry(
            0.8,
            scope="trajectory",
            sub_scores=[{"_checkpoint": {"name": "a", "status": "reached"}}],
        )
    }
    result = compose(block)
    assert result["phases"]["steps"]["n"] == 1
    assert result["phases"]["trajectory"]["n"] == 0


def test_compose_returns_none_without_evaluator_entries():
    assert compose({}) is None
    assert compose(None) is None
    assert compose({"_scored_at": "2026-01-01T00:00:00Z"}) is None


def test_compose_stamps_conflict_marker_on_outcome_lane_disagreement():
    """Two members both presenting as the unit's task-success verdict landing
    >0.5 apart never average silently — the marker rides the composite."""
    block = {"success-judge": _entry(1.0), "delivery-judge": _entry(0.2)}
    result = compose(block)
    assert result["conflict"] == {
        "lane": "output",
        "spread": 0.8,
        "members": {"success-judge": 1.0, "delivery-judge": 0.2},
    }
    # The composite still carries the mean; the marker flags it as contested.
    assert result["score"] == 0.6


def test_compose_no_conflict_within_half_point_spread():
    result = compose({"a": _entry(1.0), "b": _entry(0.6)})
    assert "conflict" not in result


def test_compose_conflict_ignores_grounding_caps_and_other_phases():
    block = {
        "success-judge": _entry(1.0),
        "grounding": _entry(0.0),
        "safety-cap": _entry(0.0, surface_area="failure_mode"),
        "step-judge": _entry(0.0, scope="step"),
    }
    assert "conflict" not in compose(block)


def test_execution_block_persisted_on_scored_unit():
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability)
    trace_id = uuid.uuid4().hex
    span = _span(project, capability, trace_id=trace_id)

    assert score_trace(trace_id, str(project.id))["status"] == "scored"
    span.refresh_from_db()
    block = (span.feedback_score or {}).get(FEEDBACK_KEY) or {}
    assert block["_execution"]["score"] == 1.0
    assert block["_execution"]["evaluations"] == 1


def test_execution_block_backfilled_on_pre_execution_row():
    """A row scored before ``_execution`` existed is rescored under the verdict
    regime and the rebuilt block carries the composed ``_execution``."""
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability, needle="Paris")
    trace_id = uuid.uuid4().hex
    span = _span(project, capability, trace_id=trace_id)
    Span.objects.filter(pk=span.pk).update(
        feedback_score={
            FEEDBACK_KEY: {
                "contains-Paris": {"score": 1.0, "outcome": "scored", "scope": "trajectory"},
                "_scored_at": "2026-01-01T00:00:00Z",
            }
        }
    )

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    span.refresh_from_db()
    block = (span.feedback_score or {}).get(FEEDBACK_KEY) or {}
    assert block["_execution"]["score"] == 1.0


def test_behaviour_filter_skip_persists_retryable_verdict():
    """A member dropped by the behaviour filter leaves a not_applicable
    Verdict row carrying the reason; once the member becomes dispatchable the
    real verdict overwrites the same series instead of being blocked by it."""
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability)
    bound_evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="ghost-outcome",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.TRAJECTORY,
        version=1,
        pass_threshold=1.0,
        config={
            "check": "contains",
            "reference": "Paris",
            "behaviour": {"behaviour_key": "ghost", "role": "outcome"},
        },
    )
    EvalSetMember.objects.create(
        eval_set=capability.active_eval_set,
        evaluator=bound_evaluator,
        role=EvalSetMember.Role.TRACE_SCORING,
        order=1,
    )
    trace_id = uuid.uuid4().hex
    span = _span(project, capability, trace_id=trace_id)

    assert score_trace(trace_id, str(project.id))["status"] == "scored"
    span.refresh_from_db()
    block = (span.feedback_score or {}).get(FEEDBACK_KEY) or {}
    assert "ghost-outcome" not in block
    assert "ghost-outcome" in block["_skipped_members"]
    skip_row = Verdict.objects.get(
        project=project, evaluator_name="ghost-outcome", target_id=span.span_id
    )
    assert skip_row.outcome == Verdict.Outcome.NOT_APPLICABLE
    assert skip_row.unmet == ["skip:behaviour-filter"]
    assert "not bound to any behaviour" in skip_row.explanation
    # The skip never counts as an evaluation.
    assert block["_execution"]["evaluations"] == 1

    # The binding is removed (e.g. a binder fix): the member now dispatches
    # and its real verdict overwrites the skip row on the same series.
    bound_evaluator.config = {"check": "contains", "reference": "Paris"}
    bound_evaluator.save(update_fields=["config"])
    assert score_trace(trace_id, str(project.id))["status"] == "scored"
    span.refresh_from_db()
    block = (span.feedback_score or {}).get(FEEDBACK_KEY) or {}
    assert "_skipped_members" not in block
    row = Verdict.objects.get(
        project=project, evaluator_name="ghost-outcome", target_id=span.span_id
    )
    assert row.outcome == Verdict.Outcome.SCORED
    assert row.score == 1.0


def _entry_point(project, capability, *, trace_id, parent_id, output, start_ns=0, status_code=1):
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=parent_id,
        project=project,
        capability=capability,
        span_type="entry_point",
        status_code=status_code,
        start_time_ns=start_ns,
        attributes={
            "overmind.span.type": "entry_point",
            "overmind.input.data": "user question",
            "overmind.output.data": output,
            "inputs": "user question",
            "outputs": output,
        },
    )


def _llm_child(project, capability, *, trace_id, parent_id, output, start_ns=0):
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=parent_id,
        project=project,
        capability=capability,
        span_type=Span.SpanType.LLM_CALL,
        start_time_ns=start_ns,
        attributes={
            "overmind.span.type": "llm_call",
            "overmind.input.data": [{"role": "user", "content": "q"}],
            "overmind.output.data": output,
        },
    )


def _batch_root(project, capability, *, trace_id, attrs=None):
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=None,
        project=project,
        capability=capability,
        span_type="batch",
        status_code=1,
        attributes={"overmind.span.type": "batch", **(attrs or {})},
    )


def test_multi_entry_point_scores_each_invocation():
    project = make_project()
    capability = make_capability(project, with_set=True)
    member = _contains_member(capability, needle="Paris")
    trace_id = uuid.uuid4().hex

    batch = _batch_root(
        project, capability, trace_id=trace_id, attrs={"inputs": {"emailCount": 2}, "outputs": []}
    )
    ep1 = _entry_point(
        project,
        capability,
        trace_id=trace_id,
        parent_id=batch.span_id,
        output="Paris is nice",
        start_ns=1,
    )
    _llm_child(
        project,
        capability,
        trace_id=trace_id,
        parent_id=ep1.span_id,
        output="Paris is nice",
        start_ns=2,
    )
    ep2 = _entry_point(
        project,
        capability,
        trace_id=trace_id,
        parent_id=batch.span_id,
        output="London calling",
        start_ns=3,
    )
    _llm_child(
        project,
        capability,
        trace_id=trace_id,
        parent_id=ep2.span_id,
        output="London calling",
        start_ns=4,
    )

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["mode"] == "multi_entry"
    assert result["invocations"] == 2

    ep1.refresh_from_db()
    ep2.refresh_from_db()
    batch.refresh_from_db()

    name = member.evaluator.name
    assert _verdict(project, ep1, name).metadata["passed"] is True
    assert _verdict(project, ep2, name).metadata["passed"] is False

    root_block = (batch.feedback_score or {}).get(FEEDBACK_KEY) or {}
    assert name not in root_block  # no collapsed whole-tree evaluator keys
    assert "invocations" in root_block
    assert root_block["invocations"]["rationale"] == "1/2 invocations passed"
    assert root_block["invocations"]["passed"] is False
    # Structured rollup alongside the human-readable summary.
    rollup = root_block["_execution"]
    assert rollup["invocations"] == 2
    assert rollup["invocations_ok"] == 1
    assert len(rollup["units"]) == 2
    assert rollup["score"] == 0.5  # mean of the 1.0 and 0.0 unit scores


def test_multi_entry_point_idempotent():
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability, needle="Paris")
    trace_id = uuid.uuid4().hex
    batch = _batch_root(project, capability, trace_id=trace_id)
    for i, out in enumerate(["Paris A", "Paris B"]):
        ep = _entry_point(
            project,
            capability,
            trace_id=trace_id,
            parent_id=batch.span_id,
            output=out,
            start_ns=i + 1,
        )
        _llm_child(
            project,
            capability,
            trace_id=trace_id,
            parent_id=ep.span_id,
            output=out,
            start_ns=i + 10,
        )

    first = score_trace(trace_id, str(project.id))
    assert first["status"] == "scored"
    second = score_trace(trace_id, str(project.id))
    assert second["status"] == "no_change"


def test_single_entry_point_as_root_has_no_invocations_summary():
    project = make_project()
    capability = make_capability(project, with_set=True)
    member = _contains_member(capability, needle="Paris")
    trace_id = uuid.uuid4().hex
    root = Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=None,
        project=project,
        capability=capability,
        span_type="entry_point",
        status_code=1,
        attributes={
            "overmind.span.type": "entry_point",
            "overmind.input.data": "q",
            "overmind.output.data": "Paris wins",
            "outputs": "Paris wins",
        },
    )
    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result.get("mode") != "multi_entry"
    root.refresh_from_db()
    block = (root.feedback_score or {}).get(FEEDBACK_KEY) or {}
    assert "invocations" not in block
    assert _verdict(project, root, member.evaluator.name).outcome == Verdict.Outcome.SCORED


def test_multi_entry_skips_error_sibling():
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability, needle="Paris")
    trace_id = uuid.uuid4().hex
    batch = _batch_root(project, capability, trace_id=trace_id)
    bad = _entry_point(
        project,
        capability,
        trace_id=trace_id,
        parent_id=batch.span_id,
        output="Paris",
        start_ns=1,
        status_code=2,
    )
    good = _entry_point(
        project,
        capability,
        trace_id=trace_id,
        parent_id=batch.span_id,
        output="Paris",
        start_ns=2,
        status_code=1,
    )
    _llm_child(
        project, capability, trace_id=trace_id, parent_id=good.span_id, output="Paris", start_ns=3
    )

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["invocations"] == 2
    assert result["invocations_ok"] == 1

    bad.refresh_from_db()
    good.refresh_from_db()
    batch.refresh_from_db()
    assert (bad.feedback_score or {}).get(FEEDBACK_KEY) is None
    assert (good.feedback_score or {}).get(FEEDBACK_KEY)
    assert ((batch.feedback_score or {}).get(FEEDBACK_KEY) or {})["invocations"][
        "rationale"
    ] == "1/2 invocations passed"


def _turn_span(project, capability, *, trace_id, parent_id, output, start_ns=0):
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=parent_id,
        project=project,
        capability=capability,
        span_type="function",
        status_code=1,
        start_time_ns=start_ns,
        attributes={
            "overmind.unit_kind": "turn",
            "overmind.input.data": "user question",
            "overmind.output.data": output,
            "inputs": "user question",
            "outputs": output,
        },
    )


def _run_root(project, capability, *, trace_id, output="run final"):
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=None,
        project=project,
        capability=capability,
        span_type="entry_point",
        status_code=1,
        start_time_ns=0,
        attributes={
            "overmind.span.type": "entry_point",
            "overmind.unit_kind": "run",
            "overmind.input.data": "user question",
            "overmind.output.data": output,
            "outputs": output,
        },
    )


def _turn_with_llm(project, capability, *, trace_id, parent_id, output, idx):
    turn = _turn_span(
        project, capability, trace_id=trace_id, parent_id=parent_id, output=output, start_ns=idx + 1
    )
    _llm_child(
        project,
        capability,
        trace_id=trace_id,
        parent_id=turn.span_id,
        output=output,
        start_ns=idx + 10,
    )
    return turn


def test_turn_stamped_spans_score_each_turn_not_the_run_root():
    project = make_project()
    capability = make_capability(project, with_set=True)
    # Turn slices carry unit-grain claims; a trajectory-grain claim would bind
    # once at the terminal turn instead of grading every turn.
    member = _contains_member(capability, needle="Paris", scope=Evaluator.Scope.TURN)
    trace_id = uuid.uuid4().hex

    root = _run_root(project, capability, trace_id=trace_id, output="Paris final")
    turns = [
        _turn_with_llm(
            project, capability, trace_id=trace_id, parent_id=root.span_id, output=out, idx=i
        )
        for i, out in enumerate(["Paris one", "Paris two", "London three"])
    ]

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["mode"] == "multi_entry"
    assert result["invocations"] == 3

    unit_span_ids = set(
        TaskExecution.objects.filter(project=project, trace_id=trace_id).values_list(
            "unit_span_id", flat=True
        )
    )
    assert unit_span_ids == {t.span_id for t in turns}

    name = member.evaluator.name
    for turn, expected in zip(turns, [True, True, False], strict=True):
        assert _verdict(project, turn, name).metadata["passed"] is expected

    root.refresh_from_db()
    root_block = (root.feedback_score or {}).get(FEEDBACK_KEY) or {}
    assert name not in root_block
    assert root_block["invocations"]["rationale"] == "2/3 invocations passed"


def _handoff_trace(project, cap_a, cap_b, *, trace_id):
    """Capability A runs two turn units, then hands off to capability B for
    one unit that carries delivery (last unit = trace terminal fallback)."""
    root = _run_root(project, cap_a, trace_id=trace_id, output="Beta three")
    turns = [
        _turn_with_llm(project, cap, trace_id=trace_id, parent_id=root.span_id, output=out, idx=i)
        for i, (cap, out) in enumerate(
            [(cap_a, "Alpha one"), (cap_a, "Alpha two"), (cap_b, "Beta three")]
        )
    ]
    return root, turns


def test_multi_capability_trace_scores_each_unit_under_its_own_capability():
    project = make_project()
    cap_a = make_capability(project, name="Alpha", with_set=True)
    cap_b = make_capability(project, name="Beta", with_set=True)
    member_a = _contains_member(cap_a, needle="Alpha", scope=Evaluator.Scope.TURN)
    member_b = _contains_member(cap_b, needle="Beta", scope=Evaluator.Scope.TURN)
    trace_id = uuid.uuid4().hex
    root, turns = _handoff_trace(project, cap_a, cap_b, trace_id=trace_id)

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["mode"] == "multi_entry"
    assert result["invocations"] == 3

    executions = {
        e.unit_span_id: e for e in TaskExecution.objects.filter(project=project, trace_id=trace_id)
    }
    assert set(executions) == {t.span_id for t in turns}
    assert executions[turns[0].span_id].capability_id == cap_a.id
    assert executions[turns[1].span_id].capability_id == cap_a.id
    assert executions[turns[2].span_id].capability_id == cap_b.id

    name_a, name_b = member_a.evaluator.name, member_b.evaluator.name
    for turn in turns[:2]:
        verdict = _verdict(project, turn, name_a)
        assert verdict.metadata["passed"] is True
        assert verdict.evaluator_id == member_a.evaluator_id
    verdict_b = _verdict(project, turns[2], name_b)
    assert verdict_b.metadata["passed"] is True
    assert verdict_b.evaluator_id == member_b.evaluator_id

    # No verdict leakage across the capability boundary.
    a_targets = set(
        Verdict.objects.filter(project=project, evaluator_name=name_a).values_list(
            "target_id", flat=True
        )
    )
    b_targets = set(
        Verdict.objects.filter(project=project, evaluator_name=name_b).values_list(
            "target_id", flat=True
        )
    )
    assert a_targets == {turns[0].span_id, turns[1].span_id}
    assert b_targets == {turns[2].span_id}

    root.refresh_from_db()
    root_block = (root.feedback_score or {}).get(FEEDBACK_KEY) or {}
    assert root_block["invocations"]["rationale"] == "3/3 invocations passed"


def test_trajectory_grain_binds_at_each_capabilitys_last_unit():
    project = make_project()
    cap_a = make_capability(project, name="Alpha", with_set=True)
    cap_b = make_capability(project, name="Beta", with_set=True)
    _contains_member(cap_a, needle="Alpha", scope=Evaluator.Scope.TURN)
    traj_a = _contains_member(cap_a, needle="two", scope=Evaluator.Scope.TRAJECTORY)
    traj_b = _contains_member(cap_b, needle="three", scope=Evaluator.Scope.TRAJECTORY)
    trace_id = uuid.uuid4().hex
    _, turns = _handoff_trace(project, cap_a, cap_b, trace_id=trace_id)

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"

    for turn in turns:
        turn.refresh_from_db()
    name_traj_a, name_traj_b = traj_a.evaluator.name, traj_b.evaluator.name

    # A's whole-run claim skips A's mid-run unit and binds at A's LAST unit,
    # not at the trace terminal (which belongs to B).
    block_1 = (turns[0].feedback_score or {}).get(FEEDBACK_KEY) or {}
    assert name_traj_a in block_1.get("_skipped_members", [])
    assert _verdict(project, turns[1], name_traj_a).metadata["passed"] is True
    # B's binds at the trace terminal, which is B's only unit.
    assert _verdict(project, turns[2], name_traj_b).metadata["passed"] is True
    assert not Verdict.objects.filter(
        project=project, target_id=turns[2].span_id, evaluator_name=name_traj_a
    ).exists()

    # The grain skip persists a retryable Verdict row carrying the reason.
    skip_row = Verdict.objects.get(
        project=project, evaluator_name=name_traj_a, target_id=turns[0].span_id
    )
    assert skip_row.outcome == Verdict.Outcome.NOT_APPLICABLE
    assert skip_row.unmet == ["skip:grain"]
    assert "trajectory-grain claim" in skip_row.explanation


def test_unattributed_unit_is_skipped_not_misattributed():
    project = make_project()
    cap_a = make_capability(project, name="Alpha", with_set=True)
    member = _contains_member(cap_a, needle="Alpha", scope=Evaluator.Scope.TURN)
    trace_id = uuid.uuid4().hex
    root = _run_root(project, cap_a, trace_id=trace_id, output="Alpha final")
    turns = []
    for i, (cap, out) in enumerate(
        [(cap_a, "Alpha one"), (None, "Alpha mid"), (cap_a, "Alpha three")]
    ):
        turn = _turn_span(
            project, cap, trace_id=trace_id, parent_id=root.span_id, output=out, start_ns=i + 1
        )
        _llm_child(
            project, cap, trace_id=trace_id, parent_id=turn.span_id, output=out, start_ns=i + 10
        )
        turns.append(turn)

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["invocations"] == 3

    # The unattributed middle unit is never scored under A's eval set…
    turns[1].refresh_from_db()
    assert (turns[1].feedback_score or {}).get(FEEDBACK_KEY) is None
    # …but its execution row still materializes, unbound and flagged.
    middle = TaskExecution.objects.get(project=project, unit_span_id=turns[1].span_id)
    assert middle.capability_id is None
    assert middle.binding_source == TaskExecution.BindingSource.UNBOUND
    assert "missing_capability" in middle.route_flags

    name = member.evaluator.name
    for turn in (turns[0], turns[2]):
        assert _verdict(project, turn, name).metadata["passed"] is True


def test_trace_capability_fallback_declines_on_multi_capability_trace():
    """Identity-less spans reuse the trace's capability only when it is
    unambiguous; a handoff trace must leave them unbound."""
    from overbae.api.otlp import _find_existing_capability_for_trace

    project = make_project()
    cap_a = make_capability(project, name="Alpha")
    cap_b = make_capability(project, name="Beta")
    trace_id = uuid.uuid4().hex
    _span(project, cap_a, trace_id=trace_id)

    assert _find_existing_capability_for_trace(trace_id, project) == cap_a

    Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=None,
        project=project,
        capability=cap_b,
        attributes={},
    )
    assert _find_existing_capability_for_trace(trace_id, project) is None


def test_error_root_multi_entry_scores_clean_units():
    """A root stamped error (e.g. cancelled teardown after delivery) must not
    void a multi-unit run: clean units score, error units are filtered per
    unit, executions materialize for every unit."""
    project = make_project()
    capability = make_capability(project, with_set=True)
    member = _contains_member(capability, needle="Paris", scope=Evaluator.Scope.TURN)
    trace_id = uuid.uuid4().hex

    root = Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=None,
        project=project,
        capability=capability,
        span_type="entry_point",
        status_code=2,
        start_time_ns=0,
        attributes={"overmind.span.type": "entry_point", "overmind.unit_kind": "run"},
    )
    turns = []
    for i, out in enumerate(["Paris one", "Paris two", "cancelled"]):
        turn = _turn_span(
            project,
            capability,
            trace_id=trace_id,
            parent_id=root.span_id,
            output=out,
            start_ns=i + 1,
        )
        _llm_child(
            project,
            capability,
            trace_id=trace_id,
            parent_id=turn.span_id,
            output=out,
            start_ns=i + 10,
        )
        turns.append(turn)
    Span.objects.filter(pk=turns[2].pk).update(status_code=2)

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["mode"] == "multi_entry"
    assert result["invocations"] == 3

    unit_span_ids = set(
        TaskExecution.objects.filter(project=project, trace_id=trace_id).values_list(
            "unit_span_id", flat=True
        )
    )
    assert unit_span_ids == {t.span_id for t in turns}

    name = member.evaluator.name
    for turn in turns[:2]:
        assert _verdict(project, turn, name).metadata["passed"] is True
    turns[2].refresh_from_db()
    assert (turns[2].feedback_score or {}).get(FEEDBACK_KEY) is None


def _qual_turn(project, capability, *, trace_id, parent_id, qualname, output, start_ns):
    ns, _, fn = qualname.rpartition(".")
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=parent_id,
        project=project,
        capability=capability,
        span_type="function",
        status_code=1,
        start_time_ns=start_ns,
        attributes={
            "overmind.unit_kind": "turn",
            "code.namespace": ns,
            "code.function.name": fn,
            "overmind.input.data": "user question",
            "overmind.output.data": output,
            "outputs": output,
        },
    )


def test_interior_turn_folds_into_enclosing_execution_as_step_coverage():
    """A turn bound to the same behaviour as its enclosing unit — and not
    itself a task entry — is internal fan-out: step coverage on the enclosing
    execution, never a sibling row judged against the whole-task rubric."""
    project = make_project()
    capability = make_capability(project, with_set=True)
    member = _contains_member(capability, needle="Paris", scope=Evaluator.Scope.TURN)
    behaviour = make_behaviour(
        capability,
        "standard-research-pipeline",
        "app.agent.conduct_research",
        ["app.agent.conduct_research", "app.agent.write_report"],
    )
    trace_id = uuid.uuid4().hex
    root = _run_root(project, capability, trace_id=trace_id, output="Paris final")
    pipeline = _qual_turn(
        project,
        capability,
        trace_id=trace_id,
        parent_id=root.span_id,
        qualname="app.agent.conduct_research",
        output="Paris pipeline",
        start_ns=1,
    )
    subs = [
        _qual_turn(
            project,
            capability,
            trace_id=trace_id,
            parent_id=pipeline.span_id,
            qualname="app.agent._process_sub_query",
            output=f"Paris sub {i}",
            start_ns=i + 2,
        )
        for i in range(2)
    ]
    for i, sub in enumerate(subs):
        _llm_child(
            project,
            capability,
            trace_id=trace_id,
            parent_id=sub.span_id,
            output="Paris",
            start_ns=i + 10,
        )

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["mode"] == "multi_entry"
    assert result["invocations"] == 1

    (execution,) = TaskExecution.objects.filter(project=project, trace_id=trace_id)
    assert execution.unit_span_id == pipeline.span_id
    assert execution.behaviour_id == behaviour.id
    assert execution.observed_route["step_coverage"] == [
        {"span_id": subs[0].span_id, "entry_qualname": "app.agent._process_sub_query"},
        {"span_id": subs[1].span_id, "entry_qualname": "app.agent._process_sub_query"},
    ]
    # The folded turns' spans stay inside the enclosing unit's evidence tree.
    assert "app.agent._process_sub_query" in execution.observed_route["anchors"]

    name = member.evaluator.name
    assert _verdict(project, pipeline, name).metadata["passed"] is True
    for sub in subs:
        sub.refresh_from_db()
        assert (sub.feedback_score or {}).get(FEEDBACK_KEY) is None


def test_turn_matching_a_behaviour_entry_anchor_mints_its_own_execution():
    """A task grain exists at this entry symbol (browser-use's Agent.step →
    agent-step-cycle), so the turn is an execution even inside an enclosing
    unit bound to the same behaviour."""
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability, needle="Paris", scope=Evaluator.Scope.TURN)
    behaviour = make_behaviour(capability, "agent-step-cycle", "app.agent.step", ["app.agent.step"])
    trace_id = uuid.uuid4().hex
    root = _run_root(project, capability, trace_id=trace_id, output="Paris final")
    outer = _qual_turn(
        project,
        capability,
        trace_id=trace_id,
        parent_id=root.span_id,
        qualname="app.agent.step",
        output="Paris outer",
        start_ns=1,
    )
    inner = _qual_turn(
        project,
        capability,
        trace_id=trace_id,
        parent_id=outer.span_id,
        qualname="app.agent.step",
        output="Paris inner",
        start_ns=2,
    )

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["invocations"] == 2

    executions = list(TaskExecution.objects.filter(project=project, trace_id=trace_id))
    assert {e.unit_span_id for e in executions} == {outer.span_id, inner.span_id}
    assert all(e.behaviour_id == behaviour.id for e in executions)
    assert all("step_coverage" not in e.observed_route for e in executions)


def test_handoff_turn_mints_its_own_execution():
    """A turn entering a different capability binds that capability's
    behaviour — never the enclosing one's — so it always stays an execution."""
    project = make_project()
    cap_a = make_capability(project, name="Alpha", with_set=True)
    cap_b = make_capability(project, name="Beta", with_set=True)
    _contains_member(cap_a, needle="Alpha", scope=Evaluator.Scope.TURN)
    _contains_member(cap_b, needle="Beta", scope=Evaluator.Scope.TURN)
    pipeline_task = make_behaviour(cap_a, "pipeline", "app.a.run", ["app.a.run"])
    review_task = make_behaviour(cap_b, "review", "app.b.review", ["app.b.review"])
    trace_id = uuid.uuid4().hex
    root = _run_root(project, cap_a, trace_id=trace_id, output="Alpha final")
    outer = _qual_turn(
        project,
        cap_a,
        trace_id=trace_id,
        parent_id=root.span_id,
        qualname="app.a.run",
        output="Alpha outer",
        start_ns=1,
    )
    outer.attributes = {**outer.attributes, "overmind.capability.id": str(cap_a.id)}
    outer.save(update_fields=["attributes"])
    inner = _qual_turn(
        project,
        cap_b,
        trace_id=trace_id,
        parent_id=outer.span_id,
        qualname="app.b.review",
        output="Beta inner",
        start_ns=2,
    )
    inner.attributes = {**inner.attributes, "overmind.capability.id": str(cap_b.id)}
    inner.save(update_fields=["attributes"])

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["invocations"] == 2

    executions = {
        e.unit_span_id: e for e in TaskExecution.objects.filter(project=project, trace_id=trace_id)
    }
    assert set(executions) == {outer.span_id, inner.span_id}
    assert executions[outer.span_id].capability_id == cap_a.id
    assert executions[outer.span_id].behaviour_id == pipeline_task.id
    assert executions[inner.span_id].capability_id == cap_b.id
    assert executions[inner.span_id].behaviour_id == review_task.id


def _keyed_flat_trace(project, capability, *, trace_id):
    """The mis-instrumented LangGraph shape: one entry_point root (itself
    mis-stamped with the LAST phase's key), flat keyed children, no turn spans."""
    root = _run_root(project, capability, trace_id=trace_id, output="London decision")
    root.attributes = {**root.attributes, "overmind.behaviour.key": "portfolio-manager"}
    root.save(update_fields=["attributes"])
    children = []
    for i, (key, out) in enumerate(
        [
            ("analyst-tool-loop", "Paris analysis"),
            ("analyst-tool-loop", "Paris again"),
            ("portfolio-manager", "London decision"),
        ]
    ):
        span = _llm_child(
            project,
            capability,
            trace_id=trace_id,
            parent_id=root.span_id,
            output=out,
            start_ns=i + 1,
        )
        span.attributes = {**span.attributes, "overmind.behaviour.key": key}
        span.save(update_fields=["attributes"])
        children.append(span)
    return root, children


def test_flat_trace_with_multiple_declared_keys_carves_per_key_units():
    project = make_project()
    capability = make_capability(project, with_set=True)
    member = _contains_member(capability, needle="Paris", scope=Evaluator.Scope.TURN)
    analyst = make_behaviour(
        capability, "analyst-tool-loop", "app.agent.analyst", ["app.agent.analyst"]
    )
    portfolio = make_behaviour(
        capability, "portfolio-manager", "app.agent.portfolio", ["app.agent.portfolio"]
    )
    trace_id = uuid.uuid4().hex
    root, children = _keyed_flat_trace(project, capability, trace_id=trace_id)

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert result["mode"] == "multi_entry"
    assert result["invocations"] == 2

    executions = {
        e.unit_span_id: e for e in TaskExecution.objects.filter(project=project, trace_id=trace_id)
    }
    # One execution per key, keyed on the key's earliest span; the mis-stamped
    # root never binds the run to the leaf behaviour.
    assert set(executions) == {children[0].span_id, children[2].span_id}
    assert executions[children[0].span_id].behaviour_id == analyst.id
    assert executions[children[2].span_id].behaviour_id == portfolio.id
    assert all(
        e.binding_source == TaskExecution.BindingSource.DECLARED for e in executions.values()
    )

    name = member.evaluator.name
    assert _verdict(project, children[0], name).metadata["passed"] is True
    assert _verdict(project, children[2], name).metadata["passed"] is False

    root.refresh_from_db()
    root_block = (root.feedback_score or {}).get(FEEDBACK_KEY) or {}
    assert name not in root_block
    assert root_block["invocations"]["rationale"] == "1/2 invocations passed"


def test_key_segmented_rescore_replaces_stale_root_execution():
    """A trace scored as one root unit before segmentation existed re-carves on
    the next pass: the root-keyed row reconciles away, per-key rows replace it,
    and a further re-run is a no-op."""
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability, needle="Paris", scope=Evaluator.Scope.TURN)
    make_behaviour(capability, "analyst-tool-loop", "app.agent.analyst", ["app.agent.analyst"])
    make_behaviour(capability, "portfolio-manager", "app.agent.portfolio", ["app.agent.portfolio"])
    trace_id = uuid.uuid4().hex
    root, children = _keyed_flat_trace(project, capability, trace_id=trace_id)
    TaskExecution.objects.create(
        project=project, capability=capability, trace_id=trace_id, unit_span_id=root.span_id
    )

    assert score_trace(trace_id, str(project.id))["status"] == "scored"
    unit_span_ids = set(
        TaskExecution.objects.filter(project=project, trace_id=trace_id).values_list(
            "unit_span_id", flat=True
        )
    )
    assert unit_span_ids == {children[0].span_id, children[2].span_id}

    assert score_trace(trace_id, str(project.id))["status"] == "no_change"
    assert TaskExecution.objects.filter(project=project, trace_id=trace_id).count() == 2


def _swept_trace_ids(monkeypatch) -> list[str]:
    from overbae.tasks import trace_scoring as trace_scoring_tasks

    enqueued: list[str] = []
    monkeypatch.setattr(
        trace_scoring_tasks.score_trace,
        "delay",
        lambda *, trace_id, project_id: enqueued.append(trace_id),
    )
    trace_scoring_tasks.sweep_unscored_traces()
    return enqueued


def test_sweep_enqueues_unscored_root(monkeypatch):
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability)
    trace_id = uuid.uuid4().hex
    _span(project, capability, trace_id=trace_id)

    assert _swept_trace_ids(monkeypatch) == [trace_id]


def test_sweep_enqueues_partial_block(monkeypatch):
    """A block missing an enabled member's entry (its evaluator raised on the
    first pass) must be re-fired, not skipped for having the key."""
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability, needle="Paris")
    _contains_member(capability, needle="France")
    trace_id = uuid.uuid4().hex
    span = _span(project, capability, trace_id=trace_id)
    Span.objects.filter(pk=span.pk).update(
        feedback_score={
            FEEDBACK_KEY: {
                "contains-Paris": {"score": 1.0, "outcome": "scored"},
                "_scored_at": "2026-01-01T00:00:00Z",
            }
        }
    )

    assert _swept_trace_ids(monkeypatch) == [trace_id]


def test_sweep_refires_after_contract_change(monkeypatch):
    project = make_project()
    capability = make_capability(project, with_set=True)
    member = _contains_member(capability, needle="Paris")
    trace_id = uuid.uuid4().hex
    _span(project, capability, trace_id=trace_id)

    assert score_trace(trace_id, str(project.id))["status"] == "scored"
    assert _swept_trace_ids(monkeypatch) == []

    # An in-place grading refresh bumps updated_at past the finished pass.
    member.evaluator.config = {"check": "contains", "reference": "Paris!"}
    member.evaluator.save()
    assert _swept_trace_ids(monkeypatch) == [trace_id]


def test_sweep_covers_root_attributed_only_through_children(monkeypatch):
    """``score_trace`` scores a trace whose root has no capability by falling back
    to an attributed child, so the sweep backstop must enqueue those roots."""
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability)
    trace_id = uuid.uuid4().hex
    root = _span(project, None, trace_id=trace_id)
    Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=root.span_id,
        project=project,
        capability=capability,
        attributes={"overmind.output.data": "The capital of France is Paris."},
    )

    assert _swept_trace_ids(monkeypatch) == [trace_id]

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"


def test_sweep_skips_agent_without_eval_set(monkeypatch):
    project = make_project()
    capability = make_capability(project)
    trace_id = uuid.uuid4().hex
    _span(project, capability, trace_id=trace_id)

    assert _swept_trace_ids(monkeypatch) == []


def test_sweep_includes_error_root_with_turn_units(monkeypatch):
    """Mirrors the service gate: an error root with multi-entry turn units is
    still scoreable, so the sweep must keep backing it up (deferred resume)."""
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability, needle="Paris")
    trace_id = uuid.uuid4().hex
    root = _span(project, capability, trace_id=trace_id)
    Span.objects.filter(pk=root.pk).update(status_code=2)
    for i in range(2):
        _turn_span(
            project,
            capability,
            trace_id=trace_id,
            parent_id=root.span_id,
            output="Paris",
            start_ns=i + 1,
        )

    assert _swept_trace_ids(monkeypatch) == [trace_id]


def test_sweep_still_excludes_single_entry_error_root(monkeypatch):
    """A single-entry error trace skips without a ScoringPass, so sweeping it
    would re-enqueue forever."""
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability, needle="Paris")
    trace_id = uuid.uuid4().hex
    root = _span(project, capability, trace_id=trace_id)
    Span.objects.filter(pk=root.pk).update(status_code=2)

    assert _swept_trace_ids(monkeypatch) == []


def test_sweep_excludes_orphan_function_fragment(monkeypatch):
    """A boundary-less single-function-span trace skips as an orphan fragment
    without a ScoringPass, so sweeping it would re-enqueue forever."""
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability, needle="Paris")
    trace_id = uuid.uuid4().hex
    span = _span(project, capability, trace_id=trace_id)
    Span.objects.filter(pk=span.pk).update(span_type="function")

    assert _swept_trace_ids(monkeypatch) == []
    result = score_trace(trace_id, str(project.id))
    assert result["reason"] == "orphan_fragment"


def test_scoring_preserves_foreign_feedback_keys():
    """No writer may replace ``feedback_score`` wholesale: keys written by
    other producers (API feedback, historical flat scores) must survive."""
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability)
    trace_id = uuid.uuid4().hex
    span = _span(project, capability, trace_id=trace_id)
    Span.objects.filter(pk=span.pk).update(
        feedback_score={"correctness": 0.4, "user_thumbs": {"up": True}}
    )

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"

    span.refresh_from_db()
    assert span.feedback_score["correctness"] == 0.4
    assert span.feedback_score["user_thumbs"] == {"up": True}
    assert FEEDBACK_KEY in span.feedback_score


def test_invocations_summary_preserves_foreign_feedback_keys():
    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability, needle="Paris")
    trace_id = uuid.uuid4().hex
    batch = Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=None,
        project=project,
        capability=capability,
        span_type="batch",
        status_code=1,
        attributes={"overmind.span.type": "batch"},
    )
    Span.objects.filter(pk=batch.pk).update(feedback_score={"correctness": 0.9})
    for i, out in enumerate(["Paris A", "Paris B"]):
        _entry_point(
            project,
            capability,
            trace_id=trace_id,
            parent_id=batch.span_id,
            output=out,
            start_ns=i + 1,
        )

    result = score_trace(trace_id, str(project.id))
    assert result["mode"] == "multi_entry"

    batch.refresh_from_db()
    assert batch.feedback_score["correctness"] == 0.9
    assert "invocations" in batch.feedback_score[FEEDBACK_KEY]


def test_sweep_refires_pass_with_deferred_work(monkeypatch):
    """A finished pass that deferred or errored verdicts is resumed; the
    Verdict-series idempotency makes the resume start where it left off."""
    from overbae.models import ScoringPass

    project = make_project()
    capability = make_capability(project, with_set=True)
    _contains_member(capability, needle="Paris")
    trace_id = uuid.uuid4().hex
    _span(project, capability, trace_id=trace_id)

    assert score_trace(trace_id, str(project.id))["status"] == "scored"
    ScoringPass.objects.filter(trace_id=trace_id).update(
        verdict_counts={"scored": 0, "deferred": 1}
    )
    assert _swept_trace_ids(monkeypatch) == [trace_id]


def test_a_second_pass_is_rejected_while_one_is_in_flight():
    """Results land only at the end of a pass, so an overlapping pass would
    re-run every judge against the same unscored trace."""
    from unittest import mock

    from overbae.tasks import trace_scoring as task_module

    nested: dict[str, object] = {}

    def run_nested(trace_id, project_id, **_kwargs):
        nested["result"] = task_module.score_trace.apply(
            kwargs={"trace_id": trace_id, "project_id": project_id}
        ).get()
        return {"status": "scored"}

    with mock.patch.object(task_module, "_score_trace", side_effect=run_nested):
        outer = task_module.score_trace.apply(
            kwargs={"trace_id": "trace-1", "project_id": "project-1"}
        ).get()

    assert outer == {"status": "scored"}
    assert nested["result"] == {"status": "in_flight", "trace_id": "trace-1"}


def test_a_finished_pass_frees_the_trace_for_the_next_one():
    from unittest import mock

    from overbae.tasks import trace_scoring as task_module

    calls: list[str] = []

    def record(trace_id, _project_id, **_kwargs):
        calls.append(trace_id)
        return {"status": "scored"}

    with mock.patch.object(task_module, "_score_trace", side_effect=record):
        for _ in range(2):
            task_module.score_trace.apply(
                kwargs={"trace_id": "trace-2", "project_id": "project-1"}
            ).get()

    assert calls == ["trace-2", "trace-2"]


def test_a_failed_pass_frees_the_trace_for_the_retry():
    from unittest import mock

    from django.core.cache import cache

    from overbae.tasks import trace_scoring as task_module

    with mock.patch.object(task_module, "_score_trace", side_effect=RuntimeError("judge died")):
        task_module.score_trace.apply(kwargs={"trace_id": "trace-3", "project_id": "project-1"})

    assert cache.get("score_trace:project-1:trace-3") is None


def _keyed_child(project, capability, *, trace_id, parent_id, output, start_ns):
    """An interior function span carrying only a declared key — the interrupted
    browser-agent shape: no unit stamps, one key, so the trace carves at the
    root tier."""
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=parent_id,
        project=project,
        capability=capability,
        span_type="function",
        status_code=1,
        start_time_ns=start_ns,
        attributes={
            "overmind.behaviour.key": "agent-step-cycle",
            "inputs": "step input",
            "outputs": output,
        },
    )


def _interrupted_root_trace(project, capability, trace_id):
    """Rootless: every parent chain dead-ends in a span that never arrived."""
    head = Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id="feedfacedeadbeef",
        project=project,
        capability=capability,
        span_type="function",
        status_code=1,
        start_time_ns=1,
        attributes={
            "overmind.input.data": "What is the capital of France?",
            "overmind.output.data": "The capital of France is Paris.",
        },
    )
    for i in range(2):
        _keyed_child(
            project,
            capability,
            trace_id=trace_id,
            parent_id=head.span_id,
            output=f"Paris step {i}",
            start_ns=i + 2,
        )
    return head


def test_interrupted_unit_scores_steps_and_skips_delivery_grades():
    """The interrupted run's completed evidence still scores: the
    trajectory-grain member dispatches, the terminal-grain member persists a
    retryable skip:interrupted verdict, and no grounding verdict is minted."""
    from overbae.services.eval import dispatch

    project = make_project()
    capability = make_capability(project, with_set=True)
    step_member = _contains_member(capability, needle="Paris", scope=Evaluator.Scope.TRAJECTORY)
    terminal_member = _contains_member(
        capability, needle="capital", scope=Evaluator.Scope.FINAL_OUTPUT, order=1
    )
    trace_id = uuid.uuid4().hex
    head = _interrupted_root_trace(project, capability, trace_id)

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"

    (execution,) = TaskExecution.objects.filter(project=project, trace_id=trace_id)
    assert execution.status == TaskExecution.Status.INTERRUPTED

    verdicts = {v.evaluator_name: v for v in Verdict.objects.filter(target_id=head.span_id)}
    assert dispatch.GROUNDING_VERDICT_NAME not in verdicts

    step_verdict = verdicts[step_member.evaluator.name]
    assert step_verdict.outcome == Verdict.Outcome.SCORED
    assert step_verdict.score == 1.0

    terminal_verdict = verdicts[terminal_member.evaluator.name]
    assert terminal_verdict.outcome == Verdict.Outcome.NOT_APPLICABLE
    assert terminal_verdict.unmet == [dispatch.SKIP_INTERRUPTED]
    assert dispatch.is_skip_verdict(terminal_verdict)
    assert "interrupted" in terminal_verdict.explanation

    head.refresh_from_db()
    block = (head.feedback_score or {}).get(FEEDBACK_KEY) or {}
    assert block["_execution"]["evaluations"] == 1
    assert terminal_member.evaluator.name in block["_skipped_members"]


def test_interrupted_skip_overwrites_stale_delivery_grade():
    """Spans only arrive, so a delivery grade sitting on a still-interrupted
    unit was minted against a run that had already died — the skip replaces
    it (and a stale grounding verdict is deleted) instead of letting a
    phantom failure pollute the quality signal."""
    from overbae.services.eval import dispatch

    project = make_project()
    capability = make_capability(project, with_set=True)
    terminal_member = _contains_member(
        capability, needle="capital", scope=Evaluator.Scope.FINAL_OUTPUT
    )
    trace_id = uuid.uuid4().hex
    head = _interrupted_root_trace(project, capability, trace_id)

    name = terminal_member.evaluator.name
    Verdict.objects.create(
        project=project,
        evaluator=terminal_member.evaluator,
        evaluator_name=name,
        target_kind=Verdict.TargetKind.SPAN,
        target_id=head.span_id,
        identifier="",  # deterministic members dispatch under the empty contract
        outcome=Verdict.Outcome.SCORED,
        score=0.0,
        explanation="did not deliver the requested file",
        metadata={"entry": {"score": 0.0, "passed": False, "outcome": "scored"}},
    )
    Verdict.objects.create(
        project=project,
        evaluator=None,
        evaluator_name=dispatch.GROUNDING_VERDICT_NAME,
        target_kind=Verdict.TargetKind.SPAN,
        target_id=head.span_id,
        identifier=dispatch.grounding_identifier(),
        outcome=Verdict.Outcome.SCORED,
        score=1.0,
        explanation="claims supported",
        metadata={"entry": {"score": 1.0, "outcome": "scored"}},
    )

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"

    (row,) = Verdict.objects.filter(target_id=head.span_id, evaluator_name=name)
    assert row.outcome == Verdict.Outcome.NOT_APPLICABLE
    assert row.unmet == [dispatch.SKIP_INTERRUPTED]
    assert not Verdict.objects.filter(
        target_id=head.span_id, evaluator_name=dispatch.GROUNDING_VERDICT_NAME
    ).exists()


def test_completed_trace_overwrites_interrupted_skip_with_real_verdict():
    """The retryability promise end to end: the interrupted turn's terminal
    member skips, then the root arrives, the trace re-scores as complete and
    the real verdict lands over the skip row."""
    from overbae.services.eval import dispatch

    project = make_project()
    capability = make_capability(project, with_set=True)
    member = _contains_member(capability, needle="Paris", scope=Evaluator.Scope.FINAL_OUTPUT)
    trace_id = uuid.uuid4().hex
    orphan_parent = "feedfacedeadbeef"

    turns = [
        _turn_span(
            project,
            capability,
            trace_id=trace_id,
            parent_id=orphan_parent,
            output=out,
            start_ns=i + 1,
        )
        for i, out in enumerate(["Paris one", "Paris two", "Paris three"])
    ]

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    name = member.evaluator.name
    # Rootless: the earliest turn reads as the boundary, the last is the
    # in-flight interrupted unit where the terminal-grain member skips.
    skip = Verdict.objects.get(target_id=turns[-1].span_id, evaluator_name=name)
    assert skip.unmet == [dispatch.SKIP_INTERRUPTED]

    Span.objects.create(
        span_id=orphan_parent,
        trace_id=trace_id,
        parent_span_id=None,
        project=project,
        capability=capability,
        span_type="entry_point",
        status_code=1,
        start_time_ns=0,
        attributes={"overmind.span.type": "entry_point", "overmind.unit_kind": "run"},
    )
    rescore = score_trace(trace_id, str(project.id))
    assert rescore["status"] == "scored"

    (row,) = Verdict.objects.filter(target_id=turns[-1].span_id, evaluator_name=name)
    assert row.outcome == Verdict.Outcome.SCORED
    assert row.score == 1.0


def test_sweep_refires_when_root_lands_after_pass_start(monkeypatch):
    """The single-flight race: the root lands while a sweep-driven mid-run pass
    is executing, so the root-triggered enqueue bounces off the lock and is
    dropped. The sweep must treat that finished pass as stale — it started
    before the root arrived, so its skip:interrupted terminals predate the
    run's completion — and re-drive the trace to real terminal verdicts."""
    from overbae.services.eval import dispatch

    project = make_project()
    capability = make_capability(project, with_set=True)
    member = _contains_member(capability, needle="Paris", scope=Evaluator.Scope.FINAL_OUTPUT)
    trace_id = uuid.uuid4().hex
    orphan_parent = "feedfacedeadbeef"
    turns = [
        _turn_span(
            project,
            capability,
            trace_id=trace_id,
            parent_id=orphan_parent,
            output=out,
            start_ns=i + 1,
        )
        for i, out in enumerate(["Paris one", "Paris two"])
    ]

    assert score_trace(trace_id, str(project.id))["status"] == "scored"
    name = member.evaluator.name
    skip = Verdict.objects.get(target_id=turns[-1].span_id, evaluator_name=name)
    assert skip.unmet == [dispatch.SKIP_INTERRUPTED]

    # Root received after the pass started; its live enqueue was the one that
    # bounced.
    Span.objects.create(
        span_id=orphan_parent,
        trace_id=trace_id,
        parent_span_id=None,
        project=project,
        capability=capability,
        span_type="entry_point",
        status_code=1,
        start_time_ns=0,
        attributes={"overmind.span.type": "entry_point", "overmind.unit_kind": "run"},
    )

    assert _swept_trace_ids(monkeypatch) == [trace_id]

    assert score_trace(trace_id, str(project.id))["status"] == "scored"
    (row,) = Verdict.objects.filter(target_id=turns[-1].span_id, evaluator_name=name)
    assert row.outcome == Verdict.Outcome.SCORED

    # Healed: the newest pass started after the root landed, so the sweep
    # settles.
    assert _swept_trace_ids(monkeypatch) == []
    assert not dispatch.is_skip_verdict(row)
