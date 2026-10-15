"""The small, read-only MCP surface for observability and job inspection."""

from __future__ import annotations

import datetime as dt
from typing import Any

from asgiref.sync import sync_to_async
from django.db.models import Q
from django.http import QueryDict
from django.utils import timezone

from overbae.api.filters import SpanFilter
from overbae.api.serializers import trace_status_map, trace_usage_totals
from overbae.innermind import health
from overbae.models import (
    Capability,
    ConnectorCredential,
    Dataset,
    DatasetPipelineRun,
    DatasetTransfer,
    DecisionJob,
    DeployedModel,
    FinetuningJob,
    InferenceRequest,
    ModelActivation,
    OptimizerExperiment,
    Span,
    TrainingPreparation,
)
from overbae.services import (
    inference_requests,
    model_workflows,
    operational_progress,
    training_monitoring,
)
from overbae.services.datasets import transfers, workbench
from overbae.services.deployment import deployment_progress
from overbae.services.entity_resolution import (
    resolve_capability,
    resolve_eval_run,
    resolve_session,
)
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.common import PageContract, ResourceLinkContract
from overbae.services.mcp.contracts.observability import (
    FailureRow,
    GetJobInput,
    GetJobOutput,
    HealthMetric,
    InspectCapabilityHealthInput,
    InspectCapabilityHealthOutput,
    LiveScoring,
    QueryFailuresInput,
    QueryFailuresOutput,
    QueryTracesInput,
    QueryTracesOutput,
    TraceHealth,
    TraceRow,
)
from overbae.services.mcp.contracts.operations import InspectOperationInput, InspectOperationOutput
from overbae.services.mcp.contracts.workbench import compact_run
from overbae.services.mcp.errors import MCPError
from overbae.services.mcp.resources import (
    dataset_run_job_payload,
    finetune_progress_payload,
    resource_link,
    safe_json,
)
from overbae.services.model_activation import activation_progress

_HEALTH_METRIC_CAP = 50
_NS_PER_SECOND = 1_000_000_000


def _link(kind: str, value: str, title: str) -> ResourceLinkContract:
    return ResourceLinkContract.model_validate(resource_link(kind, value, title))


def _capability_or_error(project, ref: str) -> Capability:
    capability, error = resolve_capability(project, ref)
    if capability is None:
        raise MCPError("capability_not_found", error or "The capability was not found.")
    return capability


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def _health_metrics(current: dict[str, dict], previous: dict[str, dict]) -> list[HealthMetric]:
    rows = []
    for name, row in current.items():
        metric = HealthMetric.model_validate(row)
        old = previous.get(name)
        if old and row["pass_rate"] is not None and old["pass_rate"] is not None:
            metric.pass_rate_delta = round(row["pass_rate"] - old["pass_rate"], 3)
        if old and row["avg_score"] is not None and old["avg_score"] is not None:
            metric.avg_score_delta = round(row["avg_score"] - old["avg_score"], 3)
        rows.append(metric)
    rows.sort(key=lambda m: next((v for v in (m.pass_rate, m.avg_score) if v is not None), 1.0))
    return rows[:_HEALTH_METRIC_CAP]


def _inspect_capability_health_sync(
    payload: InspectCapabilityHealthInput, context: MCPContext
) -> InspectCapabilityHealthOutput:
    capability = (
        _capability_or_error(context.project, payload.capability) if payload.capability else None
    )
    now = timezone.now()
    current_start = now - dt.timedelta(days=payload.days)
    previous_start = now - dt.timedelta(days=2 * payload.days)
    current = health.summarize(context.project, capability, current_start, now)
    previous = health.summarize(context.project, capability, previous_start, current_start)

    cutoff_ns = int(current_start.timestamp() * _NS_PER_SECOND)
    roots = Span.objects.filter(
        project=context.project, parent_span_id=None, start_time_ns__gte=cutoff_ns
    )
    if capability is not None:
        roots = roots.filter(capability=capability)
    root_rows = list(roots.values_list("duration_ns", "status_code")[:20_000])
    durations = sorted(duration or 0 for duration, _status in root_rows)
    errors = sum(1 for _duration, status in root_rows if status == 2)
    trace_values: dict[str, Any] = {
        "n": len(root_rows),
        "errors": errors,
        "error_rate": _rate(errors, len(root_rows)),
        "avg_duration_ms": None,
        "p95_duration_ms": None,
    }
    if durations:
        trace_values["avg_duration_ms"] = round(sum(durations) / len(durations) / 1_000_000, 1)
        p95_index = min(len(durations) - 1, -(-95 * len(durations) // 100) - 1)
        trace_values["p95_duration_ms"] = round(durations[p95_index] / 1_000_000, 1)

    scoring = LiveScoring.model_validate(
        {**current, "metrics": _health_metrics(current["metrics"], previous["metrics"])}
    )
    return InspectCapabilityHealthOutput(
        days=payload.days,
        capability=capability.slug if capability is not None else None,
        scoring=scoring,
        traces=TraceHealth(**trace_values),
        resource_links=[_link("capabilities", str(capability.id), capability.name)]
        if capability is not None
        else [],
        summary=(
            f"{trace_values['n']} traces, {scoring.scored} scored spans"
            + (f" for {capability.name}." if capability is not None else ".")
        ),
    )


def _query_failures_sync(payload: QueryFailuresInput, context: MCPContext) -> QueryFailuresOutput:
    capability = _capability_or_error(context.project, payload.capability)
    since = timezone.now() - dt.timedelta(days=payload.since_days)
    failures: list[FailureRow] = []
    links = [_link("capabilities", str(capability.id), capability.name)]
    for raw in health.failures(context.project, capability, since, payload.limit):
        trace_id = raw["trace_id"]
        row = FailureRow(
            trace_ref=f"traces:{trace_id}",
            trace_id=trace_id,
            summary=raw["summary"][:500],
            failed_scores=[
                {**failed, "reason": failed["reason"][:500]} for failed in raw["failed"][:50]
            ],
            tools=raw["tools"][:100],
            resource=_link("traces", trace_id, f"Trace {trace_id}"),
        )
        failures.append(row)
        links.append(row.resource)
    return QueryFailuresOutput(
        capability=capability.slug,
        since_days=payload.since_days,
        failures=failures,
        n=len(failures),
        resource_links=links,
        summary=f"{len(failures)} failure{'s' if len(failures) != 1 else ''} for {capability.name}.",
    )


def _trace_filter_data(payload: QueryTracesInput, context: MCPContext) -> dict[str, str]:
    data = {"project": str(context.project.id), "all_spans": str(payload.all_spans).lower()}
    if payload.capability:
        data["capability"] = str(_capability_or_error(context.project, payload.capability).id)
    if payload.session:
        session, error = resolve_session(context.project, payload.session)
        if session is None:
            raise MCPError("invalid_filter", error or "The session filter is invalid.")
        data["session"] = str(session.id)
    values = payload.model_dump(exclude_none=True, mode="json")
    direct_fields = (
        "trace_id",
        "signal_event",
        "span_id",
        "name",
        "operation",
        "service_name",
        "span_type",
        "model",
        "has_model",
        "unbound",
    )
    for field in direct_fields:
        if field in values:
            data[field] = (
                str(values[field]).lower()
                if isinstance(values[field], bool)
                else str(values[field])
            )
    for source, target in (
        ("min_duration_ms", "min_duration_ms"),
        ("max_duration_ms", "max_duration_ms"),
        ("total_tokens_gte", "total_tokens__gte"),
        ("total_tokens_lte", "total_tokens__lte"),
        ("total_cost_gte", "total_cost__gte"),
        ("total_cost_lte", "total_cost__lte"),
        ("start_after", "received_at__gte"),
        ("start_before", "received_at__lte"),
        ("start_time_ns__gte", "start_time_ns__gte"),
        ("start_time_ns__lte", "start_time_ns__lte"),
        ("status_code", "status_code"),
    ):
        if source in values:
            data[target] = str(values[source])
    if payload.status is not None:
        data["has_error"] = str(payload.status == "error").lower()
    elif payload.has_error is not None:
        data["has_error"] = str(payload.has_error).lower()
    return data


def _query_dict(data: dict[str, str]) -> QueryDict:
    query = QueryDict("", mutable=True)
    query.update(data)
    return query


def _page_offset(cursor: str | None, offset: int) -> int:
    if cursor is None:
        return offset
    try:
        value = int(cursor)
    except ValueError:
        raise MCPError("invalid_filter", "The trace cursor is invalid.") from None
    if value < 0:
        raise MCPError("invalid_filter", "The trace cursor is invalid.")
    return value


def _page(limit: int, offset: int, total: int, row_count: int) -> PageContract:
    has_more = offset + row_count < total
    return PageContract(
        limit=limit,
        offset=offset,
        total=total,
        has_more=has_more,
        next_cursor=str(offset + row_count) if has_more else None,
    )


def _query_traces_sync(payload: QueryTracesInput, context: MCPContext) -> QueryTracesOutput:
    query = Span.objects.filter(project=context.project).select_related("capability")
    trace_filter = SpanFilter(
        data=_query_dict(_trace_filter_data(payload, context)), queryset=query
    )
    if not trace_filter.is_valid():
        raise MCPError("invalid_filter", "The trace filters are invalid.")
    query = trace_filter.qs
    if payload.search:
        query = query.filter(
            Q(name__icontains=payload.search)
            | Q(service_name__icontains=payload.search)
            | Q(trace_id__icontains=payload.search)
            | Q(span_id__icontains=payload.search)
        )
    query = query.order_by(payload.ordering)
    offset = _page_offset(payload.cursor, payload.offset)
    total = query.count()
    spans = list(query[offset : offset + payload.limit])
    trace_ids = [span.trace_id for span in spans]
    usage = trace_usage_totals([context.project.id], trace_ids)
    statuses = trace_status_map([context.project.id], list(dict.fromkeys(trace_ids)))
    rows = [
        TraceRow(
            trace_id=span.trace_id,
            span_id=span.span_id,
            name=span.name,
            capability=span.capability.slug if span.capability_id else None,
            span_type=span.span_type,
            operation=span.operation,
            service_name=span.service_name,
            status_code=span.status_code,
            duration_ms=round(span.duration_ns / 1_000_000, 1),
            start_time_ns=span.start_time_ns,
            received_at=span.received_at,
            total_tokens=(usage.get(span.trace_id) or {}).get("total_tokens"),
            total_cost=(usage.get(span.trace_id) or {}).get("total_cost"),
            model=(usage.get(span.trace_id) or {}).get("model"),
            trace_status=statuses.get(span.trace_id, "live"),
            eval_score=(span.eval_attributes or {}).get("score"),
            trajectory_score=((span.eval_attributes or {}).get("trajectory") or {}).get("score"),
            resource=_link("traces", span.trace_id, f"Trace {span.trace_id}"),
        )
        for span in spans
    ]
    return QueryTracesOutput(
        traces=rows,
        n=len(rows),
        page=_page(payload.limit, offset, total, len(rows)),
        all_spans=payload.all_spans,
        resource_links=[row.resource for row in rows],
        summary=f"{len(rows)} of {total} trace{'s' if total != 1 else ''}.",
    )


_JOB_ALIASES = {
    "finetune": "finetune_job",
    "optimizer": "optimizer_experiment",
    "optimizer_run": "optimizer_experiment",
}


def _job_uuid(ref: str):
    import uuid

    try:
        return uuid.UUID(ref)
    except ValueError:
        return None


def _get_job_sync(payload: GetJobInput, context: MCPContext) -> GetJobOutput:
    kind = _JOB_ALIASES.get(payload.kind, payload.kind)
    ref = payload.id
    normalized_id = _job_uuid(ref)
    job_error = None
    progress = None
    details: dict[str, Any] = {}
    created_at = None
    updated_at = None
    completed_at = None
    label = kind
    underlying: list[ResourceLinkContract] = []
    operation_ref = None

    if kind == "dataset_transfer":
        job = DatasetTransfer.objects.filter(project=context.project, pk=normalized_id).first()
        if job is None:
            raise MCPError("resource_not_found", "The transfer was not found in this project.")
        created_at, updated_at, completed_at = job.created_at, job.updated_at, job.published_at
        label, status = "Dataset file transfer", job.state
        details = transfers.describe(job)
        progress = {
            "bytes_received": details["received"],
            "bytes_total": details["size"],
            "dispatch": details["dispatch"],
        }
        primary = _link("jobs", f"dataset_transfer/{job.pk}", label)
        for field in ("id", "eval_id"):
            if job.result.get(field):
                underlying.append(_link("datasets", job.result[field], "Transferred dataset"))
    elif kind == "inference_request":
        job = InferenceRequest.objects.filter(project=context.project, pk=normalized_id).first()
        if job is None:
            raise MCPError("resource_not_found", "Inference request not found in this project")
        created_at, updated_at, completed_at = job.created_at, job.updated_at, job.completed_at
        label, status, job_error = "Inference request", job.state, job.error_code or None
        details = inference_requests.describe(job)
        primary = _link("jobs", f"inference_request/{job.pk}", label)
    elif kind in model_workflows.MODELS:
        job = model_workflows.find(context.project, kind, normalized_id) if normalized_id else None
        if job is None:
            raise MCPError("resource_not_found", "The workflow was not found in this project")
        created_at, updated_at = job.created_at, job.updated_at
        label, status, job_error = job.name, job.state, getattr(job, "error", None) or None
        progress = safe_json(model_workflows.describe(kind, job))
        primary = _link("jobs", f"{kind}/{job.pk}", label[:160])
    elif kind == "training_preparation":
        job = (
            TrainingPreparation.objects.filter(
                cell__dataset__project=context.project, id=normalized_id
            ).first()
            if normalized_id
            else None
        )
        if job is None:
            raise MCPError("resource_not_found", "The training preparation was not found.")
        created_at, updated_at = job.created_at, job.touched_at
        completed_at = job.touched_at if job.state in {"ready", "failed"} else None
        label, status, job_error = "Training preparation", job.state, job.error or None
        progress = safe_json(job.report)
        details = safe_json(job.config)
        training_job_id = (
            job.training_jobs.order_by("created_at").values_list("id", flat=True).first()
        )
        details["training_job"] = str(training_job_id) if training_job_id else None
        primary = _link("jobs", f"training_preparation/{job.id}", label)
    elif kind == "eval_run":
        job, error = resolve_eval_run(context.project, ref)
        if job is None:
            raise MCPError("resource_not_found", error or "The evaluation run was not found.")
        created_at, updated_at, completed_at = job.created_at, job.updated_at, job.completed_at
        label, status, job_error = job.name, job.status, job.error or None
        details = {
            "mode": job.mode,
            "label": job.label,
            "group": str(job.group),
            "is_baseline": job.is_baseline,
            "dataset_id": str(job.dataset_id) if job.dataset_id else None,
            "summary": safe_json(job.summary or {}),
        }
        primary = _link("eval-runs", str(job.id), label)
    elif kind == "finetune_job":
        job = (
            FinetuningJob.objects.filter(project=context.project, id=normalized_id)
            .select_related("capability", "decision_job")
            .first()
            if normalized_id
            else None
        )
        if job is None:
            raise MCPError("resource_not_found", "The fine-tuning job was not found.")
        created_at, updated_at, completed_at = job.created_at, job.updated_at, job.completed_at
        label, status, job_error = job.name or job.base_model, job.status, job.error_message or None
        progress = finetune_progress_payload(job.progress or {})
        details = {
            "base_model": job.base_model,
            "provider": job.provider,
            "monitoring": training_monitoring.summary(job),
            "capability": job.capability.slug if job.capability_id else None,
            "dataset_id": str(job.dataset_id),
        }
        identity = getattr(job, "decision_job", None)
        if identity is not None:
            details["decision_job"] = str(identity.id)
            underlying.append(_link("jobs", f"decision_job/{identity.id}", "Decision job"))
        primary = _link("finetunes", str(job.id), label)
    elif kind == "decision_job":
        job = (
            DecisionJob.objects.select_related("finetuning_job")
            .filter(finetuning_job__project=context.project, pk=normalized_id)
            .first()
            if normalized_id
            else None
        )
        if job is None:
            raise MCPError("resource_not_found", "The decision job was not found.")
        run = job.finetuning_job
        created_at, updated_at, completed_at = run.created_at, run.updated_at, run.completed_at
        label, status, job_error = run.name or run.base_model, run.status, run.error_message or None
        progress = finetune_progress_payload(run.progress or {})
        details = {
            "model": run.base_model,
            "finetuning_job": str(run.id),
            "monitoring": training_monitoring.summary(run),
            "dataset_id": str(run.dataset_id),
        }
        primary = _link("jobs", f"decision_job/{job.id}", label)
        underlying.append(_link("finetunes", str(run.id), "Training run"))
        operation_ref = ("finetune_job", run.id)
    elif kind == "dataset_pipeline":
        job = (
            DatasetPipelineRun.objects.select_related("dataset", "pipeline", "source")
            .filter(dataset__project=context.project, pk=normalized_id)
            .first()
            if normalized_id
            else None
        )
        if job is None:
            raise MCPError("resource_not_found", "The dataset pipeline run was not found.")
        created_at, updated_at = job.created_at, job.updated_at
        label, status, job_error = "Dataset transformation", job.state, job.error or None
        primary = _link("jobs", f"dataset_pipeline/{job.pk}", label)
        details = compact_run(workbench.run_record(job), primary.model_dump(mode="json"))
        completed_at = job.updated_at if details["completed_at"] else None
        progress = details["result"]
        underlying.append(_link("datasets", str(job.dataset_id), "Dataset"))
    elif kind == "dataset_run":
        job = (
            Dataset.objects.filter(project=context.project, id=normalized_id)
            .select_related("capability", "active")
            .prefetch_related("cells")
            .first()
            if normalized_id
            else None
        )
        if job is None:
            raise MCPError("resource_not_found", "The dataset was not found.")
        snapshot = dataset_run_job_payload(
            job,
            resource_link("jobs", f"dataset_run/{job.id}", (job.name or "Dataset run")[:160])[
                "uri"
            ],
        )
        created_at, updated_at = job.created_at, job.updated_at
        label, status, job_error = (
            (job.name or "Dataset")[:160],
            snapshot["status"],
            snapshot["error"],
        )
        progress = snapshot["progress"]
        details = {
            "name": job.name,
            "state": job.state,
            "intent": job.intent,
            "source_kind": job.source_kind,
            "active": snapshot["active"],
            "cells": snapshot["cells"],
            "next_action": snapshot["next_action"],
            "next_actions": snapshot["next_actions"],
            "dataset": snapshot["dataset"],
        }
        primary = _link("jobs", f"dataset_run/{job.id}", label)
        underlying.append(_link("datasets", str(job.id), label))
    elif kind == "optimizer_experiment":
        job = (
            OptimizerExperiment.objects.filter(project=context.project, id=normalized_id)
            .select_related("capability")
            .first()
            if normalized_id
            else None
        )
        if job is None:
            raise MCPError("resource_not_found", "The optimizer run was not found.")
        created_at = job.created_at
        updated_at = job.updated_at
        label, status, job_error = (
            f"Optimize {job.capability.name}",
            job.status,
            job.failure_reason or None,
        )
        details = {
            "capability": job.capability.slug,
            "current_iteration": job.current_iteration,
            "num_iterations": job.num_iterations,
            "scores": safe_json(job.scores or {}),
        }
        primary = _link("optimizer-runs", str(job.id), label)
    elif kind == "model_activation":
        job = (
            ModelActivation.objects.filter(
                pk=normalized_id, capability__project=context.project
            ).first()
            if normalized_id
            else None
        )
        if job is None:
            raise MCPError("resource_not_found", "The model activation was not found.")
        created_at, completed_at = job.started_at, job.completed_at
        label, status, job_error = "Model activation", job.stage, job.error or None
        progress = activation_progress(job)
        primary = _link("jobs", f"model_activation/{job.id}", label)
    elif kind == "deployment":
        query = DeployedModel.objects.filter(project=context.project).select_related(
            "finetuning_job"
        )
        job = query.filter(id=normalized_id).first() if normalized_id else None
        if job is None:
            job = query.filter(model_id=ref).first()
        if job is None:
            raise MCPError("resource_not_found", "The deployment was not found.")
        created_at, updated_at, completed_at = (
            job.created_at,
            job.status_changed_at,
            job.deployed_at,
        )
        label, status, job_error = job.model_id, job.status, job.error_message or None
        progress = deployment_progress(job)
        details = {
            "model_id": job.model_id,
            "base_model_id": job.base_model_id,
            "inference_contract": job.inference_contract,
            "inference_url": job.inference_url or None,
            "finetune_job_id": str(job.finetuning_job_id) if job.finetuning_job_id else None,
        }
        primary = _link("deployments", str(job.id), label)
    elif kind == "connector_sync":
        job = (
            ConnectorCredential.objects.filter(project=context.project, id=normalized_id).first()
            if normalized_id
            else None
        )
        if job is None:
            raise MCPError("resource_not_found", "The connector was not found.")
        created_at, updated_at = job.created_at, job.updated_at
        label, status = job.name, job.sync_status
        job_error = "Connector sync failed." if job.sync_error else None
        details = {
            "connector_type": job.connector_type,
            "sync": {
                "status": job.sync_status,
                "auto_sync_enabled": job.auto_sync_enabled,
                "poll_interval_seconds": job.poll_interval_seconds,
                "last_synced_at": job.last_synced_at,
                "next_poll_at": job.next_poll_at,
                "backfill_imported": job.backfill_imported,
                "backfill_total": job.backfill_total,
                "total_spans_imported": job.total_spans_imported,
                "total_traces_imported": job.total_traces_imported,
                "has_error": bool(job.sync_error),
            },
        }
        primary = _link("connectors", str(job.id), label)
    else:
        raise MCPError("invalid_filter", "The requested job kind is not supported.")

    operation = operational_progress.latest(context.project.pk, *(operation_ref or (kind, job.id)))
    if operation:
        details["operation"] = operation
        underlying.append(_link("operations", operation["id"], "Operational timeline"))
    return GetJobOutput(
        kind=kind,
        id=str(job.id),
        status=status,
        label=label,
        job_error=str(job_error)[:1_000] if job_error else None,
        created_at=created_at,
        updated_at=updated_at,
        completed_at=completed_at,
        progress=progress,
        details=details,
        resource=primary,
        resource_links=[primary, *underlying],
        summary=f"{label[: 237 - len(status)]}: {status}.",
    )


def inspect_operation_sync(payload, context):
    try:
        result = operational_progress.inspect(
            context.project.pk, payload.operation, after=payload.after, limit=payload.limit
        )
    except operational_progress.OperationNotFoundError as exc:
        raise MCPError("resource_not_found", str(exc)) from None
    return InspectOperationOutput(
        summary=f"{result['stage']}: {result['status']}.",
        operation=result,
        resource=_link("operations", str(payload.operation), "Operational timeline"),
    )


def register_observability_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    catalog.register(
        ToolDefinition(
            name="inspect_operation",
            title="Inspect operation",
            description="Read a durable operational timeline with cursor pagination. Passive: never starts provider work. Observation, heartbeat and forward progress are separate facts.",
            input_model=InspectOperationInput,
            output_model=InspectOperationOutput,
            read_only=True,
            idempotent=True,
            open_world=False,
            required_scopes=frozenset({"overmind:read"}),
            cost_class="free",
            async_mode="sync",
        ),
        sync_to_async(inspect_operation_sync, thread_sensitive=True),
    )

    definitions = [
        (
            "inspect_capability_health",
            "Inspect capability health",
            "Aggregate live span scores per metric, scoring coverage, trace volume, error rate "
            "and latency, with deltas against the previous window.",
            InspectCapabilityHealthInput,
            InspectCapabilityHealthOutput,
            _inspect_capability_health_sync,
            "compute",
        ),
        (
            "query_failures",
            "Query capability failures",
            "Return recent traces with a failing span or trajectory score for one capability, "
            "with the failing metrics and their reasons.",
            QueryFailuresInput,
            QueryFailuresOutput,
            _query_failures_sync,
            "compute",
        ),
        (
            "query_traces",
            "Query traces",
            "Query project traces with filters and bounded cursor or offset pagination.",
            QueryTracesInput,
            QueryTracesOutput,
            _query_traces_sync,
            "free",
        ),
        (
            "get_job",
            "Get job",
            "Read a normalized status snapshot for a project job. Workshop snapshots retain "
            "measured progress, checks and identities; read evidence_resource for retained "
            "row examples and preview samples, without starting work.",
            GetJobInput,
            GetJobOutput,
            _get_job_sync,
            "free",
        ),
    ]
    for name, title, description, input_model, output_model, function, cost_class in definitions:
        catalog.register(
            ToolDefinition(
                name=name,
                title=title,
                description=description,
                input_model=input_model,
                output_model=output_model,
                read_only=True,
                idempotent=True,
                open_world=False,
                required_scopes=frozenset({"overmind:read"}),
                cost_class=cost_class,
                async_mode="sync",
            ),
            sync_to_async(function, thread_sensitive=True),
        )
