"""Capacity demand comes from durable waiting receipts, not a bounded broker queue."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Min, Q
from django.utils import timezone

from overbae.models import DatasetImport, DatasetPipelineRun
from overbae.models.eval_generation import EvalGenerationWork

NAMESPACE = "Overmind/Queues"
WORKER_SERVICES = {
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
    imports = DatasetImport.objects.filter(
        Q(state__in=["queued", "running"]) | newly_blocked
    ).aggregate(
        waiting=Count("pk", filter=Q(state="queued")),
        running=Count("pk", filter=Q(state="running")),
        blocked=Count("pk", filter=newly_blocked),
        oldest=Min("queued_at", filter=Q(state="queued")),
    )
    evaluation = EvalGenerationWork.objects.filter(sample__run__status="running").aggregate(
        waiting=Count("pk", filter=Q(state__in=["waiting", "queued"])),
        running=Count("pk", filter=Q(state="running")),
        blocked=Count("pk", filter=Q(state="unknown")),
        oldest_waiting=Min("sample__created_at", filter=Q(state="waiting")),
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
    for key in ("waiting", "running", "blocked"):
        evaluation[key] += workshop[key]
    evaluation["oldest"] = min(
        (stamp for stamp in (evaluation["oldest"], workshop["oldest"]) if stamp is not None),
        default=None,
    )
    return {
        "landing": imports,
        "batch": evaluation,
        "interactive": {"waiting": 0, "running": 0, "blocked": 0, "oldest": None},
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
            "BacklogPerWorker": (workload["waiting"] + workload["running"]) / max(workers, 1),
            "OldestQueuedAgeSeconds": age,
            "MetricHeartbeat": 1,
            "NewlyBlockedImports" if queue == "landing" else "UnknownWork": workload["blocked"],
        }
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
