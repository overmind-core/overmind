"""Capacity demand comes from durable waiting receipts, not a bounded broker queue."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.postgres.aggregates import ArrayAgg
from django.db.models import Count, Exists, Min, OuterRef, Q
from django.utils import timezone

from overbae.models import (
    DatasetImport,
    DatasetLandingWork,
    DatasetPipelineRun,
    EvalRunRow,
    LiveScoringWork,
)

NAMESPACE = "Overmind/Queues"
WORKER_SERVICES = {
    "io_traces": "celery-traces-worker",
    "landing": "celery-landing-worker",
    "batch": "celery-batch-worker",
    "interactive": "celery-interactive-worker",
}
SYSTEM_BLOCKS = ("queue_timeout", "worker_timeout", "dispatch_failed", "attempts_exhausted")
NEWLY_BLOCKED_SECONDS = 5 * 60


def read_workloads():
    newly_blocked = Q(
        state="blocked",
        failure_code__in=SYSTEM_BLOCKS,
        updated_at__gte=timezone.now() - timedelta(seconds=NEWLY_BLOCKED_SECONDS),
    )
    legacy_imports = DatasetImport.objects.annotate(
        has_admission=Exists(DatasetLandingWork.objects.filter(pk=OuterRef("pk")))
    ).filter(has_admission=False)
    imports = legacy_imports.filter(Q(state__in=["queued", "running"]) | newly_blocked).aggregate(
        waiting=Count("pk", filter=Q(state="queued")),
        running=Count("pk", filter=Q(state="running")),
        blocked=Count("pk", filter=newly_blocked),
        oldest=Min("queued_at", filter=Q(state="queued")),
        projects=ArrayAgg(
            "dataset__project_id",
            distinct=True,
            filter=Q(state__in=["queued", "running"]),
            default=[],
        ),
    )
    landing_blocked = Q(
        state="failed",
        failure_code__in=SYSTEM_BLOCKS,
        finished_at__gte=timezone.now() - timedelta(seconds=NEWLY_BLOCKED_SECONDS),
    )
    landing = DatasetLandingWork.objects.filter(
        Q(state__in=["waiting", "admitted", "running"]) | landing_blocked
    ).aggregate(
        waiting=Count("pk", filter=Q(state__in=["waiting", "admitted"])),
        running=Count("pk", filter=Q(state="running")),
        blocked=Count("pk", filter=landing_blocked),
        oldest=Min("queued_at", filter=Q(state__in=["waiting", "admitted"])),
    )
    for key in ("waiting", "running", "blocked"):
        imports[key] += landing[key]
    ages = [imports["oldest"], landing["oldest"]]
    imports["oldest"] = min((stamp for stamp in ages if stamp is not None), default=None)
    projects = set(imports.pop("projects"))
    projects.update(
        DatasetLandingWork.objects.filter(state__in=["waiting", "admitted", "running"])
        .values_list("project_id", flat=True)
        .distinct()[: settings.DATASET_LANDING_MAX_IN_FLIGHT]
    )
    imports["scaling_demand"] = min(settings.DATASET_LANDING_MAX_IN_FLIGHT, len(projects))
    evaluation = EvalRunRow.objects.filter(run__status="running").aggregate(
        waiting=Count("pk", filter=Q(state__in=["waiting", "queued"])),
        running=Count("pk", filter=Q(state="running")),
        blocked=Count("pk", filter=Q(state="unknown")),
        oldest_waiting=Min("created_at", filter=Q(state="waiting")),
        oldest_queued=Min("queued_at", filter=Q(state="queued")),
    )
    ages = [evaluation.pop(key) for key in ("oldest_waiting", "oldest_queued")]
    evaluation["oldest"] = min((stamp for stamp in ages if stamp is not None), default=None)
    workshop = DatasetPipelineRun.objects.filter(state__in=["queued", "running"]).aggregate(
        waiting=Count("pk", filter=Q(state="queued")),
        running=Count("pk", filter=Q(state="running")),
        blocked=Count("pk", filter=Q(state="running", lease_until__isnull=True)),
        oldest=Min("created_at", filter=Q(state="queued")),
    )
    recent_expiry = Q(
        error="worker admission expired",
        finished_at__gte=timezone.now() - timedelta(seconds=NEWLY_BLOCKED_SECONDS),
    )
    scoring = LiveScoringWork.objects.filter(
        Q(state__in=["waiting", "queued", "running"]) | recent_expiry
    ).aggregate(
        waiting=Count("pk", filter=Q(state__in=["waiting", "queued"])),
        running=Count("pk", filter=Q(state="running")),
        blocked=Count("pk", filter=recent_expiry),
        oldest=Min("created_at", filter=Q(state__in=["waiting", "queued"])),
    )
    project_demand = (
        LiveScoringWork.objects.filter(state__in=["waiting", "queued", "running"])
        .values("project_id")
        .annotate(total=Count("pk"))
    )
    scoring["scaling_demand"] = min(
        max(1, settings.LIVE_SCORING_MAX_IN_FLIGHT),
        sum(
            min(row["total"], max(1, settings.LIVE_SCORING_MAX_PER_PROJECT))
            for row in project_demand
        ),
    )
    return {
        "io_traces": scoring,
        "landing": imports,
        "batch": evaluation,
        "interactive": workshop,
    }


def metric_data(workloads, running_workers, *, cluster, now=None):
    now = now or timezone.now()
    points = []
    for queue, workload in workloads.items():
        workers = max(0, running_workers.get(queue, 0))
        age = max(0, (now - workload["oldest"]).total_seconds()) if workload["oldest"] else 0
        values = {
            "WaitingWork": workload["waiting"],
            "RunningWork": workload["running"],
            "RunningWorkers": workers,
            "MissingWorkers": int(workers == 0),
            "BacklogPerWorker": workload.get(
                "scaling_demand", workload["waiting"] + workload["running"]
            )
            / max(workers, 1),
            "OldestQueuedAgeSeconds": age,
            "MetricHeartbeat": 1,
            "NewlyBlockedImports" if queue == "landing" else "UnknownWork": workload["blocked"],
        }
        if queue == "landing":
            values["ScalingQueuedAgeSeconds"] = (
                age
                if workload.get("scaling_demand", workload["waiting"] + workload["running"])
                > workers
                else 0
            )
        dimensions = [
            {"Name": "ClusterName", "Value": cluster},
            {"Name": "Queue", "Value": queue},
        ]
        for name, value in values.items():
            points.append(
                {
                    "MetricName": name,
                    "Dimensions": dimensions,
                    "Timestamp": now,
                    "Value": value,
                    "Unit": "Seconds" if name.endswith("Seconds") else "Count",
                }
            )
    return points
