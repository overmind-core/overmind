import gzip
import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest
from django.db import transaction
from django.utils import timezone

from modal_shared.training_monitoring import fingerprint
from overbae.models import Dataset, FinetuningJob, Project
from overbae.services import training_experience, training_monitoring

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def ingest_sink(settings):
    events = []

    class Ingest(BaseHTTPRequestHandler):
        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            events.extend(json.loads(data)["batch"])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_):
            pass

    sink = HTTPServer(("127.0.0.1", 0), Ingest)
    threading.Thread(target=sink.serve_forever, daemon=True).start()
    settings.POSTHOG_PROJECT_TOKEN = "phc_local_test"
    settings.POSTHOG_HOST = f"http://127.0.0.1:{sink.server_port}"
    yield events
    sink.shutdown()
    sink.server_close()


@pytest.fixture
def job():
    project = Project.objects.create(name="Experience journey")
    return FinetuningJob.objects.create(
        project=project,
        dataset=Dataset.objects.create(project=project),
        provider="modal",
        status="running",
        started_at=timezone.now() - timedelta(seconds=30),
    )


def check(state="completed", **values):
    return {
        "key": "1:development:2",
        "attempt": 1,
        "stream": "development",
        "step": 2,
        "state": state,
        "policy_fingerprint": "a" * 64,
        "sample_fingerprint": "b" * 64,
        "started_at": 100,
        "observed_at": 110,
        "metrics": {"eval_loss": 0.4, "generation": {"expected": 4, "scored": 2}},
        "coverage": {"expected": 8, "scored": 8},
        "facts": {"duration_seconds": 10, "secret": "sensitive source"},
        "error": {"message": "sensitive source"},
        **values,
    }


def test_receipt_delivery_tracks_availability_once_without_exporting_content(job, ingest_sink):
    examples = [{"input": "sensitive source", "output": "sensitive source"}]
    artifact = {"sha256": fingerprint(examples)}
    pending = check("running", metrics={}, coverage={"expected": 8, "scored": 0})
    training_monitoring.ingest(job, {"checks": [pending]})
    training_monitoring.ingest(job, {"checks": [check("running", artifact=artifact)]})
    for _ in range(2):
        training_monitoring.ingest(
            job,
            {
                "checks": [check(artifact=artifact)],
                "optimizer_seconds": 100,
                "monitoring_seconds": 10,
            },
        )
    training_monitoring.ingest(
        job, {"checks": [check(artifact={"sha256": fingerprint(examples), "examples": examples})]}
    )
    training_experience.flush()
    result = [e for e in ingest_sink if e["event"] == "training_result_available"]
    terminal = [e for e in ingest_sink if e["event"] == "training_check_finished"]
    assert len(result) == len(terminal) == 1
    props = result[0]["properties"]
    assert props["job_id"] == str(job.pk)
    assert props["project_id"] == str(job.project_id)
    assert props["seconds_from_job_start"] >= 30
    assert props["loss_rows_scored"] == 8
    assert props["generation_rows_scored"] == 2
    assert terminal[0]["properties"]["duration_seconds"] == 10
    assert terminal[0]["properties"]["optimizer_seconds"] == 100
    assert terminal[0]["properties"]["monitoring_seconds"] == 10
    assert "sensitive source" not in json.dumps(ingest_sink)
    assert job.validation_runs.get().evidence
    assert job.validation_runs.get().facts["delivery"]["first_result_at"]


def test_rollback_disabled_analytics_and_sink_failure_do_not_change_receipts(
    job, ingest_sink, settings
):
    with pytest.raises(ValueError, match="rollback"), transaction.atomic():
        training_monitoring.ingest(job, {"checks": [check()]})
        raise ValueError("rollback")
    training_experience.flush()
    assert ingest_sink == []
    settings.POSTHOG_PROJECT_TOKEN = ""
    with patch("posthog.Posthog.capture", side_effect=AssertionError("analytics disabled")):
        training_monitoring.ingest(job, {"checks": [check()]})
    assert job.validation_runs.get().state == "completed"
    settings.POSTHOG_PROJECT_TOKEN = "phc_local_test"
    with patch("posthog.Posthog.capture", side_effect=RuntimeError("analytics unavailable")):
        training_monitoring.ingest(job, {"checks": [check(key="1:development:4", step=4)]})
    assert job.validation_runs.count() == 2


@pytest.mark.parametrize("resume", [False, None])
def test_verified_checkpoint_is_reported_once_and_resume_remains_explicit(job, ingest_sink, resume):
    manifest = {"path": "sensitive source", "files": {}}
    checkpoint = {
        "key": "1:2",
        "attempt": 1,
        "step": 2,
        "state": "available",
        "manifest": manifest,
        "identity": fingerprint(manifest),
        "verification": {"reload_verified": True, "resume_supported": resume},
    }
    for _ in range(2):
        training_monitoring.ingest(job, {"checkpoints": [checkpoint]})
    training_experience.flush()
    events = [e for e in ingest_sink if e["event"] == "training_checkpoint_available"]
    assert len(events) == 1
    assert events[0]["properties"]["resume_supported"] is resume
    assert events[0]["properties"]["step"] == 2
    assert "sensitive source" not in json.dumps(ingest_sink)


def test_cancelled_check_records_one_interruption_without_claiming_a_valid_result(job, ingest_sink):
    pending = check("running", metrics={}, coverage={"expected": 8, "scored": 0})
    training_monitoring.ingest(job, {"checks": [pending]})
    for _ in range(2):
        training_monitoring.interrupt(job, reason="cancelled")
    training_experience.flush()
    assert len(ingest_sink) == 1
    assert ingest_sink[0]["event"] == "training_check_finished"
    assert ingest_sink[0]["properties"]["state"] == "interrupted"
    assert ingest_sink[0]["properties"]["loss_rows_scored"] == 0
    assert job.validation_runs.get().facts["delivery"]["finished_at"]


@pytest.mark.parametrize("loss", [None, "sensitive source", False])
def test_unavailable_loss_does_not_emit_a_usable_result(job, ingest_sink, loss):
    training_monitoring.ingest(job, {"checks": [check(metrics={"eval_loss": loss})]})
    training_experience.flush()
    assert [event["event"] for event in ingest_sink] == ["training_check_finished"]
    assert "sensitive source" not in json.dumps(ingest_sink)
    assert "first_result_at" not in job.validation_runs.get().facts["delivery"]


def test_time_to_availability_includes_receipt_commit_delay(job, ingest_sink):
    observed = [job.started_at + timedelta(seconds=30)]
    with patch(
        "overbae.services.training_experience.timezone.now", side_effect=lambda: observed[0]
    ):
        with transaction.atomic():
            training_monitoring.ingest(job, {"checks": [check()]})
            observed[0] = job.started_at + timedelta(seconds=90)
        training_experience.flush()
    event = next(e for e in ingest_sink if e["event"] == "training_result_available")
    assert event["properties"]["seconds_from_job_start"] == 90
    assert (
        job.validation_runs.get().facts["delivery"]["first_result_at"]
        == (job.started_at + timedelta(seconds=30)).isoformat()
    )
