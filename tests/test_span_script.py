"""Span script evaluator: sandbox escapes plus end-to-end score_trace."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from overbae.models import EvalSetMember, Evaluator, Verdict
from overbae.services.eval import span_script
from overbae.services.eval.trace_scoring import score_trace
from tests.factories import make_capability, make_project, make_span

pytestmark = pytest.mark.django_db

_ARTIFACT_DIR = Path(__file__).parent / ".runs" / "span_script"


# ---------------------------------------------------------------------------
# Sandbox audit / escape cases (isolated — E2E cannot cover these)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "needle"),
    [
        ("import os\ndef evaluate(span):\n    return True\n", "import"),
        ("from math import sqrt\ndef evaluate(span):\n    return True\n", "import"),
        (
            "def evaluate(span):\n    return span.__class__\n",
            "attribute '__class__'",
        ),
        (
            "def evaluate(span):\n    return '{0.__class__}'.format(span)\n",
            "format",
        ),
        (
            "def evaluate(span):\n    return open('/etc/passwd').read()\n",
            "open()",
        ),
        (
            "def evaluate(span):\n    return getattr(span, 'span_id')\n",
            "getattr()",
        ),
        ("def foo(span):\n    return True\n", "def evaluate(span)"),
        ("def evaluate(span, extra):\n    return True\n", "exactly one"),
    ],
)
def test_audit_rejects_forbidden_constructs(source, needle):
    violations = span_script.audit(source)
    assert violations
    assert any(needle in v for v in violations)


def test_audit_accepts_valid_script():
    source = """
def evaluate(span):
    cost = span.attributes.get('genai.cost', 0)
    tools = [c for c in span.children if c.span_type == 'tool_call']
    if span.parent is None:
        return None
    if any(s.status_code == 'ERROR' for s in span.siblings):
        return 0.0, 'sibling errored'
    return cost < 0.01 and len(tools) <= 3
"""
    assert span_script.audit(source) == []


def test_run_blocks_mutation_across_spans():
    source = """
def evaluate(span):
    try:
        span.attributes['x'] = 1
        mutated = True
    except TypeError:
        mutated = False
    try:
        span.span_type = 'mutated'
        field_mutated = True
    except AttributeError:
        field_mutated = False
    return (
        span.span_type == 'llm_call'
        and 'x' not in span.attributes
        and not mutated
        and not field_mutated
    )
"""
    spans = [
        {
            "span_id": "aaaaaaaaaaaaaaaa",
            "trace_id": "t" * 32,
            "parent_span_id": None,
            "span_type": "llm_call",
            "operation": "",
            "service_name": "",
            "status_code": "OK",
            "status_message": "",
            "start_time_ns": 0,
            "end_time_ns": 1,
            "duration_ns": 1,
            "attributes": {},
            "events": [],
            "resource_attrs": {},
        }
    ]
    results = span_script.run(
        [{"name": "mut", "source": source, "span_ids": ["aaaaaaaaaaaaaaaa"]}],
        spans,
    )
    result = results[("mut", "aaaaaaaaaaaaaaaa")]
    assert result.outcome == "scored"
    assert result.score == 1.0


def test_run_maps_return_contract():
    spans = [
        {
            "span_id": "bbbbbbbbbbbbbbbb",
            "trace_id": "t" * 32,
            "parent_span_id": None,
            "span_type": "workflow",
            "operation": "",
            "service_name": "",
            "status_code": "OK",
            "status_message": "",
            "start_time_ns": 0,
            "end_time_ns": 1,
            "duration_ns": 1,
            "attributes": {"n": 1},
            "events": [],
            "resource_attrs": {},
        }
    ]
    source = """
def evaluate(span):
    return 0.75, 'ok'
"""
    results = span_script.run(
        [{"name": "num", "source": source, "span_ids": ["bbbbbbbbbbbbbbbb"]}],
        spans,
    )
    result = results[("num", "bbbbbbbbbbbbbbbb")]
    assert result.outcome == "scored"
    assert result.score == 0.75
    assert result.explanation == "ok"


def test_run_timeout_errors_pending_spans():
    source = """
def evaluate(span):
    while True:
        pass
"""
    spans = [
        {
            "span_id": "cccccccccccccccc",
            "trace_id": "t" * 32,
            "parent_span_id": None,
            "span_type": "workflow",
            "operation": "",
            "service_name": "",
            "status_code": "OK",
            "status_message": "",
            "start_time_ns": 0,
            "end_time_ns": 1,
            "duration_ns": 1,
            "attributes": {},
            "events": [],
            "resource_attrs": {},
        }
    ]
    # Temporarily lower wall timeout for the test.
    original = span_script.WALL_SECONDS
    span_script.WALL_SECONDS = 2
    try:
        results = span_script.run(
            [{"name": "loop", "source": source, "span_ids": ["cccccccccccccccc"]}],
            spans,
        )
    finally:
        span_script.WALL_SECONDS = original
    result = results[("loop", "cccccccccccccccc")]
    assert result.outcome == "error"
    assert "stopped" in result.explanation or "CPU" in result.explanation or result.explanation


# ---------------------------------------------------------------------------
# E2E: score_trace with a multi-level span tree
# ---------------------------------------------------------------------------


_SCRIPT = """
def evaluate(span):
    if span.span_type == 'llm_call':
        cost = span.attributes.get('genai.cost', 0)
        tools = [c for c in span.children if c.span_type == 'tool_call']
        if span.parent is None:
            return None
        if any(s.status_code == 'ERROR' for s in span.siblings):
            return 0.0, 'sibling errored'
        return cost < 0.01 and len(tools) <= 3
    if span.span_type == 'tool_call':
        return bool(span.parent)
    return None
"""


def _script_member(capability, *, span_type: str | None = "llm_call"):
    config: dict = {"source": _SCRIPT}
    if span_type is not None:
        config["span_filter"] = {"span_type": span_type}
    evaluator = Evaluator.objects.create(
        project=capability.project,
        capability=capability,
        name="cost-and-tools",
        kind=Evaluator.Kind.SCRIPT,
        scope=Evaluator.Scope.STEP,
        surface=Evaluator.Surface.HARNESS,
        score_type=Evaluator.ScoreType.BOOLEAN,
        applicable_roles=[EvalSetMember.Role.TRACE_SCORING],
        version=1,
        config=config,
    )
    return EvalSetMember.objects.create(
        eval_set=capability.active_eval_set,
        evaluator=evaluator,
        role=EvalSetMember.Role.TRACE_SCORING,
        order=0,
    )


def test_score_trace_runs_script_per_matching_span():
    project = make_project()
    capability = make_capability(project, with_set=True)
    member = _script_member(capability, span_type="llm_call")
    trace_id = uuid.uuid4().hex

    root = make_span(
        project,
        trace_id=trace_id,
        capability=capability,
        span_type="workflow",
        start_ns=0,
        attributes={"overmind.input.data": "q", "overmind.output.data": "a"},
    )
    llm = make_span(
        project,
        trace_id=trace_id,
        capability=capability,
        parent_span_id=root.span_id,
        span_type="llm_call",
        start_ns=1,
        attributes={"genai.cost": 0.001},
    )
    tool = make_span(
        project,
        trace_id=trace_id,
        capability=capability,
        parent_span_id=llm.span_id,
        span_type="tool_call",
        start_ns=2,
        attributes={},
    )
    sibling = make_span(
        project,
        trace_id=trace_id,
        capability=capability,
        parent_span_id=root.span_id,
        span_type="llm_call",
        start_ns=3,
        status_code=2,
        attributes={"genai.cost": 0.002},
    )

    first = score_trace(trace_id, str(project.id))
    assert first["status"] == "scored"

    llm_verdict = Verdict.objects.get(
        project=project, target_id=llm.span_id, evaluator_name=member.evaluator.name
    )
    sibling_verdict = Verdict.objects.get(
        project=project, target_id=sibling.span_id, evaluator_name=member.evaluator.name
    )
    assert not Verdict.objects.filter(
        project=project, target_id=tool.span_id, evaluator_name=member.evaluator.name
    ).exists()
    assert not Verdict.objects.filter(
        project=project, target_id=root.span_id, evaluator_name=member.evaluator.name
    ).exists()

    # llm's sibling is ERROR → fail; the ERROR sibling itself has only an OK peer → pass
    assert llm_verdict.outcome == "scored"
    assert llm_verdict.score == 0.0
    assert "sibling errored" in llm_verdict.explanation
    assert sibling_verdict.outcome == "scored"
    assert sibling_verdict.score == 1.0
    assert llm_verdict.annotator_kind == Verdict.AnnotatorKind.CODE
    assert llm_verdict.identifier.startswith("script:")

    second = score_trace(trace_id, str(project.id))
    assert second["status"] == "no_change"
    assert (
        Verdict.objects.filter(project=project, evaluator_name=member.evaluator.name).count() == 2
    )

    _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    artifact = {
        "command": "uv run pytest tests/test_span_script.py::test_score_trace_runs_script_per_matching_span -q",
        "fixtures": {
            "trace_id": trace_id,
            "root": root.span_id,
            "llm": llm.span_id,
            "tool": tool.span_id,
            "sibling": sibling.span_id,
            "script": _SCRIPT.strip(),
            "span_filter": {"span_type": "llm_call"},
        },
        "observed": {
            "first_status": first["status"],
            "second_status": second["status"],
            "llm": {
                "outcome": llm_verdict.outcome,
                "score": llm_verdict.score,
                "explanation": llm_verdict.explanation,
                "identifier": llm_verdict.identifier,
            },
            "sibling": {
                "outcome": sibling_verdict.outcome,
                "score": sibling_verdict.score,
                "explanation": sibling_verdict.explanation,
            },
            "tool_verdict_count": 0,
            "root_verdict_count": 0,
        },
        "environment": ["django_db", "python3 -I span_script child"],
    }
    (_ARTIFACT_DIR / "score_trace.json").write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_script_without_filter_grades_every_span():
    project = make_project()
    capability = make_capability(project, with_set=True)
    member = _script_member(capability, span_type=None)
    trace_id = uuid.uuid4().hex
    root = make_span(
        project,
        trace_id=trace_id,
        capability=capability,
        span_type="workflow",
        attributes={"overmind.input.data": "q", "overmind.output.data": "a"},
    )
    child = make_span(
        project,
        trace_id=trace_id,
        capability=capability,
        parent_span_id=root.span_id,
        span_type="tool_call",
        start_ns=1,
    )
    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    root_v = Verdict.objects.get(
        project=project, target_id=root.span_id, evaluator_name=member.evaluator.name
    )
    child_v = Verdict.objects.get(
        project=project, target_id=child.span_id, evaluator_name=member.evaluator.name
    )
    assert root_v.outcome == "abstained"
    assert child_v.outcome == "scored"
    assert child_v.score == 1.0
