"""Shared row builders and stubs. Import what you need; tests keep their own
domain-specific wrappers on top of these."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import (
    Behaviour,
    BehaviourVersion,
    Capability,
    EvalSet,
    Project,
    Span,
    User,
    Verdict,
)


def make_user(email: str | None = None, **fields: Any) -> User:
    return User.objects.create_user(
        email=email or f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
        **fields,
    )


def auth_client(user: User) -> APIClient:
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


def make_project(**fields: Any) -> Project:
    fields.setdefault("name", "P")
    fields.setdefault("slug", f"p-{uuid.uuid4().hex[:8]}")
    return Project.objects.create(**fields)


def make_capability(
    project: Project, *, name: str = "A", with_set: bool = False, **fields: Any
) -> Capability:
    capability = Capability.objects.create(
        project=project, name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:6]}", **fields
    )
    if with_set:
        eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
        capability.active_eval_set = eval_set
        capability.save(update_fields=["active_eval_set"])
    return capability


def make_span(
    project: Project,
    *,
    trace_id: str,
    capability: Capability | None = None,
    parent_span_id: str | None = None,
    span_type: str = "",
    status_code: int = 1,
    start_ns: int = 0,
    attributes: dict[str, Any] | None = None,
    **fields: Any,
) -> Span:
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=parent_span_id,
        project=project,
        capability=capability,
        span_type=span_type,
        status_code=status_code,
        start_time_ns=start_ns,
        attributes=attributes or {},
        **fields,
    )


def make_behaviour(
    capability: Capability,
    key: str,
    entry: str,
    sequence: list[str],
    *,
    grain: str = Behaviour.Grain.TURN,
    claim: str = "code_path",
) -> Behaviour:
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


def make_verdict(
    project: Project,
    *,
    target_id: str,
    evaluator_name: str,
    score: float | None = None,
    passed: bool | None = None,
    outcome: str = Verdict.Outcome.SCORED,
    explanation: str = "",
    unmet: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    **fields: Any,
) -> Verdict:
    meta = {"passed": passed, "scope": "final_output", "grain": "terminal", "gate": False}
    if metadata:
        meta.update(metadata)
    return Verdict.objects.create(
        project=project,
        evaluator_name=evaluator_name,
        target_kind=Verdict.TargetKind.SPAN,
        target_id=target_id,
        score=score,
        outcome=outcome,
        explanation=explanation,
        unmet=unmet or [],
        metadata=meta,
        **fields,
    )


def evaluator_stub(**overrides: Any) -> SimpleNamespace:
    """Evaluator lookalike for scoring code paths that never touch the DB."""
    fields: dict[str, Any] = {
        "name": "test",
        "kind": "deterministic",
        "scope": "final_output",
        "config": {},
        "score_min": 0.0,
        "score_max": 1.0,
        "pass_threshold": None,
        "choices": [],
        "score_type": "numeric",
        "rubric_md": "",
        "checklist": [],
        "variable_mapping": [],
        "judge_model": "",
        "judge_panel": [],
        "requires_reference": False,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def expectation(
    exp_id: str = "currency",
    kind: str = "contains",
    spec: Any = "USD",
    scope: str = "trace",
    gate: bool = True,
) -> dict[str, Any]:
    return {"id": exp_id, "kind": kind, "spec": spec, "scope": scope, "gate": gate}
