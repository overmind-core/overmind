"""Lane demand comes from durable receipts, not the broker: admission keeps waiting work in
Postgres, where a broker-depth metric cannot see it."""

from datetime import timedelta

from django.db.models import Count, Min, Q
from django.utils import timezone

from overbae.lanes import LANES
from overbae.models import Dataset, DatasetImport
from overbae.models.eval_generation import EvalGenerationWork

NAMESPACE = "Overmind/Queues"
# Blocked work stays blocked until someone retries it, so an alarm on the standing
# count never clears. Alarms watch what blocked within this window.
BLOCKED_WINDOW = timedelta(minutes=5)


def read_workloads(now=None):
    now = now or timezone.now()
    recent = now - BLOCKED_WINDOW
    landing = DatasetImport.objects.filter(state__in=["queued", "running", "blocked"]).aggregate(
        queued=Count("pk", filter=Q(state="queued")),
        running=Count("pk", filter=Q(state="running")),
        newly_blocked=Count("pk", filter=Q(state="blocked", updated_at__gte=recent)),
        oldest=Min("queued_at", filter=Q(state="queued")),
    )
    # Waiting samples are held by admission, not by worker capacity; only admitted
    # work asks for slots.
    batch = EvalGenerationWork.objects.filter(
        Q(state__in=["waiting", "queued", "running"], sample__run__status="running")
        | Q(state="unknown", finished_at__gte=recent)
    ).aggregate(
        waiting=Count("pk", filter=Q(state="waiting")),
        queued=Count("pk", filter=Q(state="queued")),
        running=Count("pk", filter=Q(state="running")),
        newly_blocked=Count("pk", filter=Q(state="unknown")),
        oldest=Min("queued_at", filter=Q(state="queued")),
    )
    interactive = Dataset.objects.filter(state__in=["diagnosing", "running"]).aggregate(
        queued=Count(
            "pk",
            filter=Q(workshop_started_at__isnull=True, workshop_queued_at__isnull=False),
        ),
        running=Count("pk", filter=Q(workshop_started_at__isnull=False)),
        oldest=Min("workshop_queued_at", filter=Q(workshop_started_at__isnull=True)),
    )
    return {"landing": landing, "batch": batch, "interactive": interactive}


def metric_data(workloads, running_workers, *, cluster, now=None):
    now = now or timezone.now()
    cluster_dimensions = [{"Name": "ClusterName", "Value": cluster}]
    points = [_point("MetricHeartbeat", 1, cluster_dimensions, now)]
    for lane, workload in workloads.items():
        workers = max(0, running_workers.get(lane, 0))
        oldest = workload["oldest"]
        values = {
            "QueuedWork": workload["queued"],
            "RunningWork": workload["running"],
            "RunningWorkers": workers,
            # Dimensionless, so scaling targets 1.0 whatever a lane's process count.
            "SlotDemand": (workload["queued"] + workload["running"])
            / (max(workers, 1) * LANES[lane].concurrency),
            "OldestQueuedAgeSeconds": max(0.0, (now - oldest).total_seconds()) if oldest else 0,
        }
        if "waiting" in workload:
            values["WaitingWork"] = workload["waiting"]
        if "newly_blocked" in workload:
            values["NewlyBlockedWork"] = workload["newly_blocked"]
        dimensions = [*cluster_dimensions, {"Name": "Queue", "Value": lane}]
        points += [_point(name, value, dimensions, now) for name, value in values.items()]
    return points


def _point(name, value, dimensions, now):
    unit = "Seconds" if name.endswith("Seconds") else "None" if name == "SlotDemand" else "Count"
    return {
        "MetricName": name,
        "Dimensions": dimensions,
        "Timestamp": now,
        "Value": value,
        "Unit": unit,
    }
