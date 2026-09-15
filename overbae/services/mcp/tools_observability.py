"""The small, read-only MCP surface for observability and job inspection."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

from asgiref.sync import sync_to_async
from django.db.models import Avg, Count, Q
from django.http import QueryDict
from django.utils import timezone
from pydantic import BaseModel

from overbae.api.filters import SpanFilter, TaskExecutionFilter
from overbae.api.serializers import (
    eligible_scoring_capabilities,
    trace_status_map,
    trace_usage_totals,
    traces_with_finished_scoring_pass,
)
from overbae.models import (
    Capability,
    ConnectorCredential,
    Dataset,
    DeployedModel,
    FinetuningJob,
    OptimizerExperiment,
    Score,
    Span,
    TaskExecution,
)
from overbae.services.entity_resolution import (
    resolve_behaviour,
    resolve_capability,
    resolve_eval_run,
    resolve_session,
)
from overbae.services.live_trace_scores import (
    aggregate_live_scores,
    collect_capability_failure_rows,
    spans_with_live_scores,
)
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.common import PageContract, ResourceLinkContract
from overbae.services.mcp.contracts.observability import (
    FailureRow,
    GetJobInput,
    GetJobOutput,
    HealthEvaluator,
    InspectCapabilityHealthInput,
    InspectCapabilityHealthOutput,
    LiveTraceHealth,
    QueryFailuresInput,
    QueryFailuresOutput,
    QueryTaskExecutionsInput,
    QueryTaskExecutionsOutput,
    QueryTracesInput,
    QueryTracesOutput,
    TaskExecutionRow,
    TraceHealth,
    TraceRow,
)
from overbae.services.mcp.errors import MCPError
from overbae.services.mcp.resources import (
    dataset_run_job_payload,
    resource_link,
    safe_json,
)

_HEALTH_EVALUATOR_CAP = 50
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


def _health_rows(rows: dict[str, dict[str, Any]]) -> list[HealthEvaluator]:
    values: list[dict[str, Any]] = []
    for name, row in rows.items():
        avg_value = round(row["avg_value"], 3) if row["avg_value"] is not None else None
        values.append(
            {
                "evaluator": name,
                "n": row["n"],
                "avg_value": avg_value,
                "pass_rate": _rate(row["n_passed"], row["n_graded"]),
                "n_failed": row["n_failed"],
            }
        )
    values.sort(
        key=lambda row: next(
            (row[field] for field in ("pass_rate", "avg_value") if row[field] is not None),
            1.0,
        )
    )
    return [HealthEvaluator.model_validate(row) for row in values[:_HEALTH_EVALUATOR_CAP]]


def _add_health_deltas(current: list[HealthEvaluator], previous: list[HealthEvaluator]) -> None:
    previous_by_name = {row.evaluator: row for row in previous}
    for row in current:
        old = previous_by_name.get(row.evaluator)
        if old is None:
            continue
        if row.pass_rate is not None and old.pass_rate is not None:
            row.pass_rate_delta = round(row.pass_rate - old.pass_rate, 3)
        if row.avg_value is not None and old.avg_value is not None:
            row.avg_value_delta = round(row.avg_value - old.avg_value, 3)


def _health_aggregates(project, capability, start, end) -> dict[str, dict[str, Any]]:
    query = Score.objects.filter(
        project=project,
        outcome=Score.Outcome.SCORED,
        created_at__gte=start,
        created_at__lt=end,
    )
    if capability is not None:
        query = query.filter(evaluator__capability=capability)
    return {
        row["name"]: row
        for row in query.values("name").annotate(
            n=Count("id"),
            avg_value=Avg("value"),
            n_passed=Count("id", filter=Q(passed=True)),
            n_failed=Count("id", filter=Q(passed=False)),
            n_graded=Count("id", filter=Q(passed__isnull=False)),
        )
    }


def _inspect_capability_health_sync(
    payload: InspectCapabilityHealthInput, context: MCPContext
) -> InspectCapabilityHealthOutput:
    capability = (
        _capability_or_error(context.project, payload.capability) if payload.capability else None
    )
    now = timezone.now()
    current_start = now - dt.timedelta(days=payload.days)
    previous_start = now - dt.timedelta(days=2 * payload.days)
    current = _health_aggregates(context.project, capability, current_start, now)
    previous = _health_aggregates(context.project, capability, previous_start, current_start)
    evaluators = _health_rows(current)
    _add_health_deltas(evaluators, _health_rows(previous))

    outcome_query = Score.objects.filter(project=context.project, created_at__gte=current_start)
    if capability is not None:
        outcome_query = outcome_query.filter(evaluator__capability=capability)
    outcomes = {
        row["outcome"]: row["n"] for row in outcome_query.values("outcome").annotate(n=Count("id"))
    }
    total_passed = sum(row["n_passed"] for row in current.values())
    total_graded = sum(row["n_graded"] for row in current.values())

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

    current_live = list(
        spans_with_live_scores(
            context.project, capability=capability, since=current_start, until=now
        )
    )
    previous_live = list(
        spans_with_live_scores(
            context.project,
            capability=capability,
            since=previous_start,
            until=current_start,
        )
    )
    live_values = aggregate_live_scores(
        context.project.id,
        current_live,
        previous_scored_spans=previous_live,
        traces_in_window=trace_values["n"],
    )
    live_evaluators = [
        HealthEvaluator.model_validate(row) for row in live_values["evaluators"][:50]
    ]
    return InspectCapabilityHealthOutput(
        days=payload.days,
        capability=capability.slug if capability is not None else None,
        scores={
            "total": sum(row["n"] for row in current.values()),
            "graded": total_graded,
            "overall_pass_rate": _rate(total_passed, total_graded),
            "outcomes": outcomes,
            "evaluators": evaluators,
        },
        live_trace_scores=LiveTraceHealth(
            traces_in_window=live_values["traces_in_window"] or 0,
            traces_scored=live_values["traces_scored"],
            coverage=live_values["coverage"],
            overall_pass_rate=live_values["overall_pass_rate"],
            evaluators=live_evaluators,
        ),
        traces=TraceHealth(**trace_values),
        resource_links=[_link("capabilities", str(capability.id), capability.name)]
        if capability is not None
        else [],
        summary=(
            f"{trace_values['n']} traces, {sum(row['n'] for row in current.values())} scores"
            + (f" for {capability.name}." if capability is not None else ".")
        ),
    )


def _query_failures_sync(payload: QueryFailuresInput, context: MCPContext) -> QueryFailuresOutput:
    capability = _capability_or_error(context.project, payload.capability)
    raw_rows = collect_capability_failure_rows(
        context.project,
        capability=capability,
        since_days=payload.since_days,
        limit=payload.limit,
    )
    failures: list[FailureRow] = []
    links = [_link("capabilities", str(capability.id), capability.name)]
    for raw in raw_rows[: payload.limit]:
        trace_id = str(
            raw.get("trace_id") or str(raw.get("trace_ref") or "").removeprefix("traces:")
        )
        if not trace_id:
            continue
        row = FailureRow(
            trace_ref=str(raw.get("trace_ref") or f"traces:{trace_id}"),
            trace_id=trace_id,
            summary=str(raw.get("summary") or "")[:500],
            failed_scores=raw.get("failed_scores") or [],
            tools=[str(tool) for tool in (raw.get("tools") or [])[:100]],
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


def _execution_filter_data(
    payload: QueryTaskExecutionsInput, context: MCPContext
) -> dict[str, str]:
    data = {"project": str(context.project.id)}
    for ref, key, resolver in (
        (payload.capability, "capability", resolve_capability),
        (payload.behaviour, "behaviour", resolve_behaviour),
    ):
        if ref:
            value, error = resolver(context.project, ref)
            if value is None:
                raise MCPError("invalid_filter", error or "The task filter is invalid.")
            data[key] = str(value.id)
    values = payload.model_dump(exclude_none=True, mode="json")
    mapping = {
        "binding_source": "binding_source",
        "trace_id": "trace_id",
        "status": "status",
        "started_after": "started_at__gte",
        "started_before": "started_at__lte",
        "min_duration_ms": "min_duration_ms",
        "max_duration_ms": "max_duration_ms",
        "service_name": "service_name",
        "operation": "operation",
        "span_type": "span_type",
        "status_code": "status_code",
        "model": "model",
        "has_model": "has_model",
        "total_tokens_gte": "total_tokens__gte",
        "total_tokens_lte": "total_tokens__lte",
        "total_cost_gte": "total_cost__gte",
        "total_cost_lte": "total_cost__lte",
        "has_error": "has_error",
    }
    for source, target in mapping.items():
        if source in values:
            data[target] = (
                str(values[source]).lower()
                if isinstance(values[source], bool)
                else str(values[source])
            )
    return data


def _query_task_executions_sync(
    payload: QueryTaskExecutionsInput, context: MCPContext
) -> QueryTaskExecutionsOutput:
    query = TaskExecution.objects.filter(project=context.project).select_related(
        "capability", "behaviour"
    )
    execution_filter = TaskExecutionFilter(
        data=_query_dict(_execution_filter_data(payload, context)), queryset=query
    )
    if not execution_filter.is_valid():
        raise MCPError("invalid_filter", "The task execution filters are invalid.")
    query = execution_filter.qs.order_by("-started_at", "-created_at")
    offset = payload.offset
    total = query.count()
    executions = list(query[offset : offset + payload.limit])
    trace_ids = list(dict.fromkeys(execution.trace_id for execution in executions))
    usage = trace_usage_totals([context.project.id], trace_ids)
    capability_ids = [
        execution.capability_id for execution in executions if execution.capability_id
    ]
    eligible = eligible_scoring_capabilities([context.project.id], capability_ids)
    finished = traces_with_finished_scoring_pass([context.project.id], trace_ids)
    rows = []
    for execution in executions:
        execution_usage = usage.get(execution.trace_id) or {}
        pending = (
            execution.success_score is None
            and execution.status != TaskExecution.Status.ERROR
            and execution.trace_id not in finished
            and str(execution.capability_id) in eligible
        )
        rows.append(
            TaskExecutionRow(
                id=str(execution.id),
                capability=str(execution.capability_id) if execution.capability_id else None,
                behaviour=str(execution.behaviour_id) if execution.behaviour_id else None,
                behaviour_key=execution.behaviour.key if execution.behaviour_id else "",
                trace_id=execution.trace_id,
                unit_span_id=execution.unit_span_id,
                conversation_id=execution.conversation_id,
                binding_source=execution.binding_source,
                success_score=execution.success_score,
                session_score=execution.session_score,
                session_rationale=execution.session_rationale[:500],
                route_flags=safe_json(execution.route_flags or []),
                terminal_kind=execution.terminal_kind,
                status=execution.status,
                started_at=execution.started_at,
                duration_ms=execution.duration_ms,
                total_tokens=execution_usage.get("total_tokens"),
                total_cost=execution_usage.get("total_cost"),
                model=execution_usage.get("model"),
                scoring_pending=pending,
                resource=_link("traces", execution.trace_id, f"Trace {execution.trace_id}"),
            )
        )
    return QueryTaskExecutionsOutput(
        task_executions=rows,
        n=len(rows),
        page=_page(payload.limit, offset, total, len(rows)),
        resource_links=[row.resource for row in rows],
        summary=f"{len(rows)} of {total} task execution{'s' if total != 1 else ''}.",
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

    if kind == "eval_run":
        job, error = resolve_eval_run(context.project, ref)
        if job is None:
            raise MCPError("resource_not_found", error or "The evaluation run was not found.")
        created_at, updated_at, completed_at = job.created_at, job.updated_at, job.completed_at
        label, status, job_error = job.name, job.status, job.error or None
        details = {
            "data_source": job.data_source,
            "dataset_id": str(job.dataset_id) if job.dataset_id else None,
            "summary": safe_json(job.summary or {}),
        }
        primary = _link("eval-runs", str(job.id), label)
    elif kind == "finetune_job":
        job = (
            FinetuningJob.objects.filter(project=context.project, id=normalized_id)
            .select_related("capability")
            .first()
            if normalized_id
            else None
        )
        if job is None:
            raise MCPError("resource_not_found", "The fine-tuning job was not found.")
        created_at, updated_at, completed_at = job.created_at, job.updated_at, job.completed_at
        label, status, job_error = job.name or job.base_model, job.status, job.error_message or None
        progress = safe_json(job.progress or {})
        details = {
            "base_model": job.base_model,
            "provider": job.provider,
            "capability": job.capability.slug if job.capability_id else None,
            "dataset_id": str(job.dataset_id),
        }
        primary = _link("finetunes", str(job.id), label)
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
        label, status, job_error = (job.name or "Dataset")[:160], job.state, job.error or None
        progress = snapshot["progress"]
        details = {
            "name": job.name,
            "state": job.state,
            "intent": job.intent,
            "source_kind": job.source_kind,
            "active": snapshot["active"],
            "latest_turn": snapshot["latest_turn"],
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
    elif kind == "deployment":
        query = DeployedModel.objects.filter(project=context.project).select_related(
            "finetuning_job"
        )
        job = query.filter(id=normalized_id).first() if normalized_id else None
        if job is None:
            job = query.filter(model_id=ref).first()
        if job is None:
            raise MCPError("resource_not_found", "The deployment was not found.")
        created_at, completed_at = job.created_at, job.deployed_at
        label, status, job_error = job.model_id, job.status, job.error_message or None
        details = {
            "model_id": job.model_id,
            "base_model_id": job.base_model_id,
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
        summary=f"{label}: {status}.",
    )


def _async_handler(function: Callable[[BaseModel, MCPContext], BaseModel]):
    async def handler(payload: BaseModel, context: MCPContext):
        return await sync_to_async(function, thread_sensitive=True)(payload, context)

    return handler


def register_observability_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    definitions = [
        (
            "inspect_capability_health",
            "Inspect capability health",
            "Aggregate evaluator, live-score, trace-volume, error-rate and latency health.",
            InspectCapabilityHealthInput,
            InspectCapabilityHealthOutput,
            _inspect_capability_health_sync,
            "compute",
        ),
        (
            "query_failures",
            "Query capability failures",
            "Return recent scored failures for one project capability.",
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
            "query_task_executions",
            "Query task executions",
            "Query bounded behaviour-keyed task executions with filters.",
            QueryTaskExecutionsInput,
            QueryTaskExecutionsOutput,
            _query_task_executions_sync,
            "free",
        ),
        (
            "get_job",
            "Get job",
            "Read a normalized status snapshot for a project job.",
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
            _async_handler(function),
        )
