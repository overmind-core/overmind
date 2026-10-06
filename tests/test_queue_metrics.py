from datetime import timedelta

import pytest
from django.utils import timezone

from overbae.lanes import LANES
from overbae.services.queue_capacity import metric_data, read_workloads


def _values(points, queue):
    return {
        item["MetricName"]: item["Value"]
        for item in points
        if {"Name": "Queue", "Value": queue} in item["Dimensions"]
    }


def test_slot_demand_is_relative_to_the_lane_process_count():
    now = timezone.now()
    slots = LANES["interactive"].concurrency
    points = metric_data(
        {
            "interactive": {
                "queued": 3 * slots,
                "running": slots,
                "oldest": now - timedelta(minutes=4),
            }
        },
        {"interactive": 2},
        cluster="test",
        now=now,
    )
    values = _values(points, "interactive")
    assert values["SlotDemand"] == 2
    assert values["OldestQueuedAgeSeconds"] == 240
    assert values["RunningWorkers"] == 2


def test_zero_workers_still_report_demand_without_division_by_zero():
    points = metric_data(
        {"landing": {"queued": 4, "running": 0, "oldest": None, "newly_blocked": 0}},
        {},
        cluster="test",
    )
    values = _values(points, "landing")
    assert values["SlotDemand"] == 4 / LANES["landing"].concurrency
    assert values["RunningWorkers"] == 0


def test_heartbeat_is_one_cluster_sample_independent_of_any_queue():
    points = metric_data({}, {}, cluster="test")
    assert [p for p in points if p["MetricName"] == "MetricHeartbeat"] == [
        {
            "MetricName": "MetricHeartbeat",
            "Dimensions": [{"Name": "ClusterName", "Value": "test"}],
            "Timestamp": points[0]["Timestamp"],
            "Value": 1,
            "Unit": "Count",
        }
    ]


@pytest.mark.django_db
def test_imports_report_queued_demand_and_only_newly_blocked_receipts():
    from overbae.models import Dataset, DatasetImport, Project

    now = timezone.now()
    project = Project.objects.create(name="queue metric fixture", slug="queue-metrics")
    for state, age in [
        ("queued", 120),
        ("running", 300),
        ("complete", 5000),
        ("cancelled", 5000),
        ("blocked", 60),
        ("blocked", 7200),
    ]:
        dataset = Dataset.objects.create(project=project, name=f"{state}-{age}")
        receipt = DatasetImport.objects.create(
            dataset=dataset, state=state, queued_at=now - timedelta(seconds=age), inputs={}
        )
        DatasetImport.objects.filter(pk=receipt.pk).update(updated_at=now - timedelta(seconds=age))
    landing = read_workloads(now=now)["landing"]
    assert (landing["queued"], landing["running"]) == (1, 1)
    assert landing["newly_blocked"] == 1, "a receipt blocked hours ago must not hold the alarm"
    assert (now - landing["oldest"]).total_seconds() == 120


@pytest.mark.django_db
def test_eval_admission_waits_are_not_worker_demand():
    from overbae.models import EvalRun, EvalSample, EvalVariant, Project
    from overbae.models.eval_generation import EvalGenerationWork

    now = timezone.now()
    project = Project.objects.create(name="eval metric fixture", slug="eval-metrics")
    run = EvalRun.objects.create(project=project, name="run", status="running")
    variant = EvalVariant.objects.create(run=run, label="v", mode="generate")
    for index, (state, queued, finished) in enumerate(
        [
            ("waiting", None, None),
            ("waiting", None, None),
            ("queued", now - timedelta(seconds=90), None),
            ("running", now - timedelta(seconds=200), None),
            ("unknown", now - timedelta(hours=1), now - timedelta(seconds=30)),
            ("unknown", now - timedelta(hours=3), now - timedelta(hours=2)),
        ]
    ):
        sample = EvalSample.objects.create(run=run, variant=variant, row_index=index)
        EvalGenerationWork.objects.create(
            sample=sample, state=state, queued_at=queued, finished_at=finished
        )
    batch = read_workloads(now=now)["batch"]
    assert batch["waiting"] == 2
    assert (batch["queued"], batch["running"]) == (1, 1)
    assert batch["newly_blocked"] == 1
    assert (now - batch["oldest"]).total_seconds() == 90


def test_metrics_task_is_registered_periodic_and_never_waits_on_batch(settings):
    from overbae.celery import app

    app.loader.import_default_modules()
    name = "overbae.tasks.queue_metrics.publish"
    assert name in app.tasks
    schedule = next(item for item in settings.CELERY_BEAT_SCHEDULE.values() if item["task"] == name)
    assert schedule["schedule"] <= 60
    assert schedule["options"]["expires"] < schedule["schedule"]
    assert (
        settings.CELERY_TASK_ROUTES.get(name, {}).get("queue", settings.CELERY_TASK_DEFAULT_QUEUE)
        == "control"
    )


@pytest.mark.django_db
def test_hosted_publisher_signs_with_the_refreshing_task_role(ecs):
    from overbae.tasks.queue_metrics import publish

    ecs.short_lived_credentials = True
    ecs.running = {"celery-landing-worker": 2}
    assert publish() == {"cluster": "test-cluster", "metrics": 19}
    assert ecs.api_calls == ["DescribeServices"]
    assert ecs.credential_reads >= 2
    (headers,) = ecs.metric_requests
    assert "Credential=task-role-2/" in headers["Authorization"]
    assert "/eu-west-1/" in headers["Authorization"]
    assert headers["X-Amz-Security-Token"] == "task-role-token"


def test_publisher_outside_ecs_reports_disabled(monkeypatch):
    from overbae.tasks.queue_metrics import publish

    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    assert publish() == {"enabled": False}
