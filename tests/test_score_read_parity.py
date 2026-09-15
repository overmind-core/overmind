"""The console's score surfaces read Verdict rows plus the slim span markers.

These tests pin the parity contract from the Wave-3 consolidation: what the
verdict-backed read paths return must equal what the scorer composed, and
migration 0129's slimming/flattening must preserve every console-visible
number for legacy-shape rows.
"""

from __future__ import annotations

import importlib
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

migration_0129 = importlib.import_module("overbae.migrations.0129_slim_span_feedback_blocks")


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


LEGACY_BLOCK = {
    "delivery-judge": {
        "score": 0.8,
        "passed": None,
        "outcome": "scored",
        "rationale": "mostly delivered",
        "scope": "final_output",
        "grain": "terminal",
        "gate": False,
        "surface_area": "",
        "sub_scores": [],
        "eval_set_member_id": "m-1",
        "evaluator_id": "e-1",
        "evaluator_version": 3,
        "display_name": "Delivery",
        "scored_at": "2026-01-01T00:00:00Z",
    },
    "safety-gate": {
        "score": None,
        "passed": False,
        "outcome": "scored",
        "rationale": "violation",
        "scope": "trajectory",
    },
    "_skipped_members": ["ghost"],
    "_scored_at": "2026-01-01T00:00:00Z",
}


def test_migration_slims_legacy_block_preserving_console_numbers():
    """A pre-``_execution`` block loses its per-entry rows but the list
    surfaces keep the exact score the console showed: the plain average the
    old frontend fallback computed."""
    slim, changed = migration_0129._slim_block(dict(LEGACY_BLOCK))
    assert changed
    assert set(slim) == {"_execution", "_skipped_members", "_scored_at"}
    # Boolean fail is 0.0, graded 0.8 clamps as-is: mean 0.4 — the fallback's number.
    assert slim["_execution"]["score"] == 0.4
    assert slim["_execution"]["evaluations"] == 2
    assert slim["_execution"]["any_failed"] is True
    assert slim["_skipped_members"] == ["ghost"]

    # Blocks already carrying the composer's marker keep it verbatim (plus the
    # failure flag), and a second pass is a no-op.
    composed = {
        "judge": {"score": 1.0, "passed": True, "outcome": "scored"},
        "_execution": {"score": 0.9, "evaluations": 1, "phases": {}},
        "invocations": {"score": None, "passed": True, "outcome": "scored", "lane": "summary"},
    }
    slim, changed = migration_0129._slim_block(composed)
    assert changed
    assert slim["_execution"]["score"] == 0.9
    assert slim["_execution"]["any_failed"] is False
    assert slim["invocations"]["passed"] is True
    again, changed_again = migration_0129._slim_block(dict(slim))
    assert not changed_again
    assert again == slim


def test_migration_flattens_legacy_verdict_metadata():
    """The ``{"entry": {...}}`` transcription shape flattens to the fields the
    serializer lifts — same passed/scope/grain/gate/sub_scores the legacy
    block entry carried."""
    project = make_project()
    verdict = Verdict.objects.create(
        project=project,
        evaluator_name="delivery-judge",
        target_kind=Verdict.TargetKind.SPAN,
        target_id=uuid.uuid4().hex[:16],
        score=0.8,
        outcome=Verdict.Outcome.SCORED,
        explanation="mostly delivered",
        metadata={"entry": dict(LEGACY_BLOCK["delivery-judge"]), "coverage": 0.75},
    )
    apps = importlib.import_module("django.apps").apps
    migration_0129._flatten_verdict_metadata(apps, None)
    verdict.refresh_from_db()
    assert verdict.metadata == {
        "passed": None,
        "scope": "final_output",
        "grain": "terminal",
        "gate": False,
        "surface_area": "",
        "sub_scores": [],
        "coverage": 0.75,
    }
    data = VerdictSerializer(verdict).data
    assert data["scope"] == "final_output"
    assert data["grain"] == "terminal"
    assert data["passed"] is None
