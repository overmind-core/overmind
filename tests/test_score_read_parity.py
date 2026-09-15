"""The console's score surfaces read Verdict rows plus the slim span markers:
what the verdict-backed read paths return must equal what the scorer composed."""

from __future__ import annotations

import uuid

import pytest

from overbae.api.eval_serializers import VerdictSerializer
from overbae.models import EvalSetMember, Evaluator, Verdict
from overbae.services.eval import dispatch
from overbae.services.eval.trace_scoring import FEEDBACK_KEY, execution_score, score_trace
from overbae.services.live_trace_scores import (
    display_verdicts,
    list_trace_score_fields,
    trace_score_detail,
)
from tests.factories import make_capability, make_project, make_span

pytestmark = pytest.mark.django_db


def _scored_trace(project, capability):
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="contains-paris",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.TRAJECTORY,
        version=1,
        pass_threshold=1.0,
        config={"check": "contains", "reference": "Paris"},
    )
    EvalSetMember.objects.create(
        eval_set=capability.active_eval_set,
        evaluator=evaluator,
        role=EvalSetMember.Role.TRACE_SCORING,
        order=0,
    )
    trace_id = uuid.uuid4().hex
    span = make_span(
        project,
        trace_id=trace_id,
        capability=capability,
        attributes={
            "overmind.input.data": "What is the capital of France?",
            "overmind.output.data": "The capital of France is Paris.",
        },
    )
    assert score_trace(trace_id, str(project.id))["status"] == "scored"
    span.refresh_from_db()
    return span, evaluator


def test_verdict_reads_match_the_scorers_composition():
    """Detail chips (verdict rows) and list markers (slim block) agree: the
    chip grades recompose to exactly the persisted ``_execution`` score."""
    project = make_project()
    capability = make_capability(project, with_set=True)
    span, evaluator = _scored_trace(project, capability)

    block = (span.feedback_score or {}).get(FEEDBACK_KEY) or {}
    assert set(block) == {"_execution", "_scored_at"}  # slim: no per-entry rows

    verdicts = display_verdicts(project.id, [span.span_id])[span.span_id]
    assert [v.evaluator_name for v in verdicts] == [evaluator.name]
    entry = dispatch.block_entry(verdicts[0])
    assert entry["score"] == 1.0
    assert entry["passed"] is True
    assert entry["scope"] == "trajectory"

    assert execution_score(span.feedback_score) == block["_execution"]["score"] == 1.0
    assert block["_execution"]["any_failed"] is False

    detail = trace_score_detail(span, [span])
    assert detail["scoring_mode"] == "single"
    assert detail["trace_scores"][evaluator.name]["passed"] is True

    compact, n_scored, any_failed = list_trace_score_fields(span, verdicts)
    assert compact == {evaluator.name: {"score": 1.0, "passed": True}}
    assert n_scored == 1
    assert any_failed is False


def test_verdict_serializer_lifts_composition_fields():
    project = make_project()
    capability = make_capability(project, with_set=True)
    span, evaluator = _scored_trace(project, capability)

    verdict = Verdict.objects.get(
        project=project, target_id=span.span_id, evaluator_name=evaluator.name
    )
    data = VerdictSerializer(verdict).data
    assert data["evaluator_name"] == evaluator.name
    assert data["score"] == 1.0
    assert data["passed"] is True
    assert data["scope"] == "trajectory"
    assert data["grain"]
    assert isinstance(data["sub_scores"], list)
