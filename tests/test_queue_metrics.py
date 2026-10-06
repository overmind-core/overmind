from datetime import timedelta

import pytest
from django.utils import timezone

from overbae.services.queue_capacity import metric_data, read_workloads


def test_metrics_count_durable_waiting_not_only_admitted_broker_messages():
    now = timezone.now()
    data = metric_data(
        {
            "landing": {
                "waiting": 100,
                "running": 2,
                "oldest": now - timedelta(minutes=4),
                "blocked": 3,
            }
        },
        {"landing": 2},
        cluster="test",
        now=now,
    )
    values = {item["MetricName"]: item["Value"] for item in data}
    assert values["BacklogPerWorker"] == 51
    assert values["OldestQueuedAgeSeconds"] == 240
    assert values["BlockedImports"] == 3
    assert values["MetricHeartbeat"] == 1
    assert all(
        item["Dimensions"]
        == [{"Name": "ClusterName", "Value": "test"}, {"Name": "Queue", "Value": "landing"}]
        for item in data
    )


def test_zero_worker_capacity_is_observable_without_division_by_zero():
    data = metric_data(
        {"landing": {"waiting": 4, "running": 0, "oldest": None, "blocked": 0}}, {}, cluster="test"
    )
    values = {item["MetricName"]: item["Value"] for item in data}
    assert values["BacklogPerWorker"] == 4
    assert values["MissingWorkers"] == 1
    assert values["RunningWorkers"] == 0


@pytest.mark.django_db
def test_completed_and_cancelled_imports_do_not_request_capacity():
    from overbae.models import Dataset, DatasetImport, Project

    now = timezone.now()
    project = Project.objects.create(name="queue metric fixture", slug="queue-metrics")
    for state, age in [
        ("queued", 120),
        ("running", 300),
        ("complete", 5000),
        ("cancelled", 5000),
        ("blocked", 700),
    ]:
        dataset = Dataset.objects.create(project=project, name=state)
        DatasetImport.objects.create(
            dataset=dataset, state=state, queued_at=now - timedelta(seconds=age), inputs={}
        )
    snapshot = read_workloads()["landing"]
    assert snapshot["waiting"] == 1
    assert snapshot["running"] == 1
    assert snapshot["blocked"] == 1
    assert (now - snapshot["oldest"]).total_seconds() == 120


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
