"""Shared row builders and stubs. Import what you need; tests keep their own
domain-specific wrappers on top of these."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import (
    APIToken,
    Behaviour,
    BehaviourVersion,
    Capability,
    ConnectorCredential,
    ConnectorSyncConfig,
    EvalSet,
    Project,
    ProjectMembership,
    Span,
    User,
    Verdict,
)


def make_user(email: str | None = None, **fields: Any) -> User:
    return User.objects.create_user(
        email=email or f"u-{uuid.uuid4().hex[:8]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
        **fields,
    )


def auth_client(user: User) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def api_key_client(user: User, project: Project | None = None) -> APIClient:
    raw_key, _ = APIToken.create_for_user(user, project=project)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=raw_key)
    return client


def make_project(name: str = "P", *, member: User | None = None, **fields: Any) -> Project:
    fields.setdefault("slug", f"p-{uuid.uuid4().hex[:8]}")
    project = Project.objects.create(name=name, **fields)
    if member is not None:
        make_member(member, project)
    return project


def make_member(user: User, project: Project) -> ProjectMembership:
    return ProjectMembership.objects.create(user=user, project=project)


def member_client(project: Project) -> APIClient:
    user = make_user()
    make_member(user, project)
    return auth_client(user)


def make_connector(
    connector_type: str,
    *,
    source_project_id: str = "",
    base_url: str = "",
    api_key: str = "key",
    api_secret: str = "",
    project: Project | None = None,
    capability_mapping: dict | None = None,
    name: str = "",
    **config: Any,
) -> ConnectorCredential:
    credential = ConnectorCredential.objects.create(
        project=project or make_project(),
        name=name or f"{connector_type}-{uuid.uuid4().hex[:6]}",
        connector_type=connector_type,
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        api_version="v2" if connector_type == "langfuse" else "unknown",
        capability_mapping=capability_mapping or {},
    )
    ConnectorSyncConfig.objects.create(
        credential=credential,
        version=1,
        source_project_id=source_project_id,
        lookback_days=config.pop("lookback_days", 3),
        effective_from=timezone.now(),
        **config,
    )
    return credential


def sync_until_live(credential: ConnectorCredential, *, chunks: int = 50) -> ConnectorCredential:
    from overbae.tasks.connector_sync import sync_connector_chunk

    for _ in range(chunks):
        ConnectorCredential.objects.filter(id=credential.id).update(next_poll_at=None)
        sync_connector_chunk(str(credential.id))
        credential.refresh_from_db()
        if credential.sync_status == ConnectorCredential.SyncStatus.LIVE:
            return credential
    raise AssertionError("backfill never reached LIVE")


def prepare_training(job, fake_modal):
    from overbae.services import training_preparation

    preparation = training_preparation.for_job(job)
    training_preparation.advance(preparation.id)
    fake_modal.release("prepare_")
    training_preparation.advance(preparation.id)
    preparation.refresh_from_db()
    assert preparation.state == "ready", preparation.error
    return preparation


def reconcile_training(active_tasks: list[dict] | None = None) -> list[tuple[str, dict]]:
    from unittest.mock import MagicMock, patch

    from overbae.tasks.finetuning_reconciler import reconcile_finetuning_jobs

    sent: list[tuple[str, dict]] = []
    app = MagicMock()
    app.control.inspect.return_value.active.return_value = {"w1": active_tasks or []}
    app.control.inspect.return_value.reserved.return_value = {}
    app.control.inspect.return_value.scheduled.return_value = {}

    def _send(name, kwargs=None):
        sent.append((name, kwargs or {}))
        return MagicMock(id=str(uuid.uuid4()))

    app.send_task.side_effect = _send
    with (
        patch("overbae.celery.get_celery_app", return_value=app),
        patch("overbae.tasks.model_deployment.register_finetuned_model.delay"),
    ):
        reconcile_finetuning_jobs()
    return sent


def make_capability(
    project: Project, name: str = "A", *, with_set: bool = False, **fields: Any
) -> Capability:
    fields.setdefault("slug", f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}")
    capability = Capability.objects.create(project=project, name=name, **fields)
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
