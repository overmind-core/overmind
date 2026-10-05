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
from overbae.services.behaviour.ledger import TurnTransitions


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

    from overbae.celery import get_celery_app
    from overbae.tasks.finetuning_reconciler import reconcile_finetuning_jobs

    sent: list[tuple[str, dict]] = []
    broker = MagicMock()
    broker.active.return_value = {"w1": active_tasks or []}
    broker.reserved.return_value = {}
    broker.scheduled.return_value = {}

    def _send(name, kwargs=None):
        sent.append((name, kwargs or {}))
        return MagicMock(id=str(uuid.uuid4()))

    app = get_celery_app()
    # Celery's dispatch and broker inspection, not our code: capture sends, fake the workers.
    with (
        patch.object(app, "send_task", side_effect=_send),
        patch.object(app.control, "inspect", return_value=broker),
        patch("overbae.tasks.model_deployment.register_finetuned_model.delay"),
    ):
        reconcile_finetuning_jobs()
    return sent


def classifier_replies(fake_llm, *outcomes):
    """Script the classifier's replies in order: a list of transitions, None for an
    unparsable reply, or an exception for a provider failure. The last repeats."""
    fake_llm.forget()
    sent = []

    def current():
        outcome = outcomes[min(len(sent), len(outcomes) - 1)]
        return getattr(outcome, "parsed", outcome)

    def failing(request):
        if request.schema_name == "TurnTransitions" and isinstance(current(), Exception):
            sent.append(request)
            return True
        return False

    def reply(request):
        outcome = current()
        sent.append(request)
        if outcome is None:
            return "not json"
        if isinstance(outcome, TurnTransitions):
            return outcome.model_dump_json()
        return TurnTransitions(transitions=outcome).model_dump_json()

    fake_llm.fail(failing, 400, "down")
    fake_llm.on(lambda r: r.schema_name == "TurnTransitions", reply)
    return sent


def ingest_spans(project: Project, spans: list[dict[str, Any]], resource: dict | None = None):
    """POST spans to the OTLP endpoint as a real exporter would. Each span is
    ``{"name", "attributes", "trace_id"?, "span_id"?, "parent"?}``; ids are hex."""
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    from opentelemetry.proto.resource.v1.resource_pb2 import Resource
    from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans
    from opentelemetry.proto.trace.v1.trace_pb2 import Span as ProtoSpan

    def value(v):
        if isinstance(v, bool):
            return AnyValue(bool_value=v)
        if isinstance(v, int):
            return AnyValue(int_value=v)
        if isinstance(v, float):
            return AnyValue(double_value=v)
        return AnyValue(string_value=str(v))

    def attributes(mapping):
        return [KeyValue(key=k, value=value(v)) for k, v in (mapping or {}).items()]

    default_trace = uuid.uuid4().hex
    proto = [
        ProtoSpan(
            trace_id=bytes.fromhex(span.get("trace_id") or default_trace),
            span_id=bytes.fromhex(span.get("span_id") or uuid.uuid4().hex[:16]),
            parent_span_id=bytes.fromhex(span["parent"]) if span.get("parent") else b"",
            name=span["name"],
            start_time_unix_nano=1_000,
            end_time_unix_nano=9_000,
            attributes=attributes(span.get("attributes")),
        )
        for span in spans
    ]
    request = ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                resource=Resource(attributes=attributes(resource)),
                scope_spans=[ScopeSpans(spans=proto)],
            )
        ]
    )
    owner = make_user()
    make_member(owner, project)
    response = api_key_client(owner, project).post(
        "/api/v1/traces",
        data=request.SerializeToString(),
        content_type="application/x-protobuf",
    )
    assert response.status_code == 200, response.content
    return response


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
