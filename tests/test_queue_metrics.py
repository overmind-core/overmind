import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    assert values["NewlyBlockedImports"] == 3
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
def test_only_open_imports_request_capacity_and_only_new_system_blocks_alarm():
    from overbae.models import Dataset, DatasetImport, Project

    now = timezone.now()
    project = Project.objects.create(name="queue metric fixture", slug="queue-metrics")
    for name, state, age, code in [
        ("queued", "queued", 120, ""),
        ("running", "running", 300, ""),
        ("complete", "complete", 5000, ""),
        ("cancelled", "cancelled", 5000, ""),
        ("lost worker", "blocked", 700, "worker_timeout"),
        ("old block", "blocked", 5000, "queue_timeout"),
        ("bad rows", "blocked", 700, "import_failed"),
    ]:
        dataset = Dataset.objects.create(project=project, name=name)
        run = DatasetImport.objects.create(
            dataset=dataset,
            state=state,
            queued_at=now - timedelta(seconds=age),
            inputs={},
            failure_code=code,
        )
        if name == "old block":
            DatasetImport.objects.filter(pk=run.pk).update(updated_at=now - timedelta(minutes=6))
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


@pytest.mark.django_db
def test_publisher_uses_refreshing_task_role_instead_of_archive_keys(settings, monkeypatch):
    from overbae.tasks.queue_metrics import publish

    requests = []
    credential_reads = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            credential_reads.append(self.path)
            # The initial credentials expire soon enough to require refresh on signing.
            expiry = timezone.now() + timedelta(seconds=30 if len(credential_reads) == 1 else 3600)
            payload = json.dumps(
                {
                    "AccessKeyId": f"task-role-{len(credential_reads)}",
                    "SecretAccessKey": "task-role-secret",
                    "Token": "task-role-token",
                    "Expiration": expiry.isoformat(),
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.path, dict(self.headers)))
            ecs = self.path == "/ecs"
            body = (
                json.dumps(
                    {
                        "services": [
                            {"serviceName": "celery-landing-worker", "runningCount": 1},
                            {"serviceName": "celery-batch-worker", "runningCount": 1},
                        ]
                    }
                ).encode()
                if ecs
                else b""
            )
            self.send_response(200)
            self.send_header(
                "Content-Type", "application/x-amz-json-1.1" if ecs else "application/cbor"
            )
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "archive-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "archive-secret")
    monkeypatch.delenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", raising=False)
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", url + "/credentials")
    monkeypatch.setenv("AWS_ENDPOINT_URL_ECS", url + "/ecs")
    monkeypatch.setenv("AWS_ENDPOINT_URL_CLOUDWATCH", url + "/cloudwatch")
    settings.QUEUE_METRICS_ENABLED = True
    settings.QUEUE_METRICS_CLUSTER = "test-cluster"
    settings.AWS_REGION = "eu-west-1"
    try:
        assert publish() == {"metrics": 24}
        assert len(credential_reads) >= 2
        assert len(requests) == 2
        for _, headers in requests:
            assert "Credential=task-role-2/" in headers["Authorization"]
            assert "archive-key" not in headers["Authorization"]
            assert "/eu-west-1/" in headers["Authorization"]
            assert headers["X-Amz-Security-Token"] == "task-role-token"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_enabled_monitor_refuses_archive_credentials_without_an_ecs_role(settings, monkeypatch):
    from overbae.tasks.queue_metrics import publish

    settings.QUEUE_METRICS_ENABLED = True
    settings.QUEUE_METRICS_CLUSTER = "test-cluster"
    monkeypatch.delenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", raising=False)
    monkeypatch.delenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "archive-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "archive-secret")
    with pytest.raises(RuntimeError, match="ECS task role"):
        publish()
