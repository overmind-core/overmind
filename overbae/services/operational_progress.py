import math
from datetime import UTC, datetime

from django.db import transaction
from django.db.models import CharField, OuterRef, Subquery, Value
from django.db.models.functions import Cast, Replace
from django.utils import timezone

from overbae.models import FinetuningJob, OperationalEvent, OperationalRun

FACTS = frozenset(
    {
        "provider",
        "provider_call_id",
        "provider_input_id",
        "worker_id",
        "runtime",
        "model_id",
        "base_identity",
        "artifact_identity",
        "deployment_id",
        "active_model_id",
        "target_model_id",
        "application_connected",
        "deadline",
        "next_poll_at",
        "error_code",
        "retry_safe",
        "telemetry_available",
        "telemetry_scope",
        "provider_status",
        "attempt",
        "completed_rows",
        "total_rows",
        "committed_shards",
        "trained_steps",
        "total_steps",
        "parameters",
        "bytes",
        "backlog",
        "num_running_inputs",
        "num_total_runners",
        "restore_seconds",
        "elapsed_seconds",
        "lease_until",
        "recovery",
        "event_source",
        "process_alive",
        "source_export_rows",
        "measurement",
        "files_completed",
        "files_total",
        "run_id",
        "check_id",
        "sample_fingerprint",
        "policy_fingerprint",
        "finding_count",
    }
)
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "ready", "complete", "incompatible"})


class OperationNotFoundError(ValueError):
    pass


def timestamp(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if type(value) in (int, float) and math.isfinite(value) and value > 0:
        return datetime.fromtimestamp(value, UTC)
    if isinstance(value, str) and value:
        return timestamp(datetime.fromisoformat(value))
    return None


def facts_only(facts):
    result = {}
    for key, value in (facts or {}).items():
        if key not in FACTS or not isinstance(value, (str, int, float, bool, type(None))):
            raise ValueError("Unsupported operational fact")
        if isinstance(value, str) and len(value) > 200:
            raise ValueError("Operational fact exceeds its bound")
        if type(value) is float and not math.isfinite(value):
            raise ValueError("Operational fact must be finite")
        result[key] = value
    return result


def forward_change(previous, current):
    if any(current.get(key) != previous.get(key) for key in ("stage", "status")):
        return True
    completed, before = current.get("completed"), previous.get("completed")
    return completed is not None and (
        before is None or current.get("unit") != previous.get("unit") or completed > before
    )


def record(
    project_id,
    kind,
    reference,
    attempt,
    *,
    stage,
    status="running",
    completed=None,
    total=None,
    unit=None,
    source_at=None,
    heartbeat_at=None,
    facts=None,
):
    facts = facts_only(facts)
    now = timezone.now()
    source_at, heartbeat_at = timestamp(source_at) or now, timestamp(heartbeat_at)
    for count in (completed, total):
        if count is not None and (
            type(count) not in (int, float) or not math.isfinite(count) or count < 0
        ):
            raise ValueError("Operational counts must be finite and nonnegative")
    snapshot = {
        "stage": stage,
        "status": status,
        "completed": completed,
        "total": total,
        "unit": unit,
        "facts": facts,
    }
    with transaction.atomic():
        run, _ = OperationalRun.objects.get_or_create(
            project_id=project_id, kind=kind, reference=str(reference), attempt=str(attempt)
        )
        run = OperationalRun.objects.select_for_update().get(pk=run.pk)
        previous = run.snapshot
        run.last_observed_at = now
        if (
            run.source_at
            and source_at < run.source_at
            or (previous.get("status") in TERMINAL and status not in TERMINAL)
        ):
            run.save(update_fields=["last_observed_at"])
            return run
        fresh_heartbeat = heartbeat_at and (
            not run.last_heartbeat_at or heartbeat_at > run.last_heartbeat_at
        )
        changed = snapshot != previous
        forward = forward_change(previous, snapshot)
        if changed or fresh_heartbeat:
            run.sequence += 1
            OperationalEvent.objects.create(
                operation=run,
                sequence=run.sequence,
                event="progress" if forward else "observation" if changed else "heartbeat",
                facts={
                    **snapshot,
                    "heartbeat_at": heartbeat_at.isoformat() if heartbeat_at else None,
                },
                source_at=source_at,
                observed_at=now,
            )
        if forward:
            run.last_progress_at = source_at
        if fresh_heartbeat:
            run.last_heartbeat_at = heartbeat_at
        run.snapshot, run.source_at = snapshot, source_at
        run.save()
        return run


def summary(run):
    if run is None:
        return None
    return {
        "id": str(run.pk),
        "kind": run.kind,
        "reference": run.reference,
        "attempt": run.attempt,
        **run.snapshot,
        "event_count": run.sequence,
        "last_observed_at": run.last_observed_at.isoformat() if run.last_observed_at else None,
        "last_heartbeat_at": run.last_heartbeat_at.isoformat() if run.last_heartbeat_at else None,
        "last_progress_at": run.last_progress_at.isoformat() if run.last_progress_at else None,
        "telemetry": "recorded" if run.sequence else "unavailable",
        "provider": run.provider_state,
    }


def latest(project_id, kind, reference):
    return summary(
        OperationalRun.objects.filter(project_id=project_id, kind=kind, reference=str(reference))
        .order_by("-created_at")
        .first()
    )


def inspect(project_id, operation_id, *, after=0, limit=50):
    if not 1 <= limit <= 100 or after < 0:
        raise ValueError("Use limit 1–100 and a nonnegative event cursor")
    run = OperationalRun.objects.filter(project_id=project_id, pk=operation_id).first()
    if run is None:
        raise OperationNotFoundError("Operation not found in this project")
    events = list(run.events.filter(sequence__gt=after, sequence__lte=run.sequence)[: limit + 1])
    more = len(events) > limit
    events = events[:limit]
    return {
        **summary(run),
        "events": [
            {
                "sequence": event.sequence,
                "event": event.event,
                "source_at": event.source_at.isoformat(),
                "observed_at": event.observed_at.isoformat(),
                **event.facts,
            }
            for event in events
        ],
        "page": {
            "has_more": more,
            "next_cursor": events[-1].sequence if events else after,
            "through_sequence": run.sequence,
        },
    }


def preparation(prep, progress=None):
    progress = progress if progress is not None else (prep.report or {}).get("progress", {})
    ready = prep.state == "ready"
    rows = (prep.report or {}).get("rows") if ready else None
    uploading = progress.get("stage") == "uploading"
    return record(
        prep.cell.dataset.project_id,
        "training_preparation",
        prep.pk,
        prep.deadline.isoformat(),
        stage=progress.get("stage") or prep.state,
        status=prep.state,
        completed=rows if ready else None if uploading else progress.get("completed_rows"),
        total=rows if ready else None if uploading else progress.get("total_rows"),
        unit=None if uploading else "rows",
        source_at=progress.get("updated_at"),
        facts={
            "provider": "modal",
            "provider_call_id": prep.remote_id or None,
            "deadline": prep.deadline.isoformat(),
            "telemetry_available": bool(progress) or ready,
            "retry_safe": False,
            "source_export_rows": progress.get("completed_rows") if uploading else None,
        },
    )


def training(job):
    progress = job.progress or {}
    diagnostics = progress.get("diagnostics") or {}
    terminal = job.status in TERMINAL
    return record(
        job.project_id,
        "finetune_job",
        job.pk,
        job.remote_job_id or str(job.pk),
        stage=job.status
        if terminal
        else diagnostics.get("stage") or progress.get("stage") or job.status,
        status=job.status,
        completed=progress.get("trained_steps")
        if terminal
        else diagnostics.get("completed", progress.get("trained_steps")),
        total=progress.get("total_steps")
        if terminal
        else diagnostics.get("total", progress.get("total_steps")),
        unit="steps" if terminal else diagnostics.get("unit") or "steps",
        heartbeat_at=diagnostics.get("heartbeat_at"),
        source_at=job.completed_at if terminal else diagnostics.get("source_at"),
        facts={
            "provider": job.provider,
            "provider_call_id": job.remote_job_id or None,
            "attempt": diagnostics.get("attempt"),
            "telemetry_available": bool(diagnostics),
            "measurement": diagnostics.get("measurement"),
            "files_completed": diagnostics.get("files_completed"),
            "files_total": diagnostics.get("files_total"),
            "run_id": diagnostics.get("run_id"),
        },
    )


def reconcile_training_terminals(limit=100):
    # SQLite stores UUIDs without separators; recorded operation references use UUID text.
    latest_status = (
        OperationalRun.objects.filter(kind="finetune_job", project_id=OuterRef("project_id"))
        .annotate(job_reference=Replace("reference", Value("-"), Value("")))
        .filter(job_reference=OuterRef("operation_reference"))
        .order_by("-created_at")
        .values("snapshot__status")[:1]
    )
    jobs = (
        FinetuningJob.objects.filter(status__in=TERMINAL)
        .annotate(
            operation_reference=Replace(Cast("pk", CharField()), Value("-"), Value("")),
            operation_status=Subquery(latest_status),
        )
        .filter(operation_status__isnull=False)
        .exclude(operation_status__in=TERMINAL)
        .order_by("completed_at", "pk")[:limit]
    )
    count = 0
    for job in jobs:
        job.refresh_from_db()
        if job.status in TERMINAL:
            training(job)
            count += 1
    return count


def deployment(deployed):
    return record(
        deployed.project_id,
        "deployment",
        deployed.pk,
        deployed.deployment_generation,
        stage=deployed.deployment_stage or deployed.status,
        status=deployed.status,
        facts={
            "provider": "modal",
            "provider_call_id": deployed.deployment_call_id or None,
            "model_id": deployed.model_id,
            "deployment_id": str(deployed.pk),
            "attempt": deployed.deployment_attempts,
            "deadline": deployed.deployment_deadline.isoformat()
            if deployed.deployment_deadline
            else None,
            "retry_safe": not deployed.deployment_dispatching,
            "telemetry_available": False,
        },
    )


def activation(activation):
    capability = activation.capability
    return record(
        capability.project_id,
        "model_activation",
        activation.pk,
        activation.generation,
        stage=activation.stage,
        status=activation.stage,
        facts={
            "provider": "modal",
            "provider_call_id": activation.call_id or None,
            "active_model_id": str(capability.active_model_id)
            if capability.active_model_id
            else None,
            "target_model_id": str(activation.target_id) if activation.target_id else None,
            "application_connected": bool(capability.last_application_request_at),
            "deadline": activation.deadline.isoformat(),
            "retry_safe": False,
            "telemetry_available": False,
        },
    )
