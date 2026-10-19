import os
import socket
import tempfile
import time
import uuid

import pytest
import redis
from celery.exceptions import Retry
from factories import make_project

from overbae.models import Cell, Dataset, DatasetPipelineRun
from overbae.services.datasets import pipeline_runner
from overbae.tasks.datasets import execute_pipeline


def test_scratch_reclaims_dead_workers_but_preserves_active_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    abandoned = tmp_path / "workshop-abandoned"
    abandoned.mkdir()
    (abandoned / "output").write_bytes(b"incomplete")
    os.utime(abandoned, (time.time() - 120, time.time() - 120))
    with pipeline_runner.scratch() as active:
        assert not abandoned.exists()
        retained = active / "retained"
        retained.write_bytes(b"active")
        os.utime(active, (time.time() - 120, time.time() - 120))
        with pipeline_runner.scratch() as competing:
            assert retained.read_bytes() == b"active"
            assert competing != active
        assert not competing.exists()
    assert not active.exists()
    with pytest.raises(ValueError), pipeline_runner.scratch() as failed:
        raise ValueError("cancelled")
    assert not failed.exists()


@pytest.mark.django_db
def test_admission_connection_loss_retries_without_claiming_a_script(settings):
    dataset = Dataset.objects.create(project=make_project(), name="admission outage")
    cell = Cell.objects.create(dataset=dataset, position=0)
    run = DatasetPipelineRun.objects.create(dataset=dataset, source=cell, request_key="outage")
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        settings.CELERY_BROKER_URL = f"redis://127.0.0.1:{unavailable.getsockname()[1]}/15"
        execute_pipeline.push_request(id=uuid.uuid4().hex, called_directly=False, is_eager=True)
        try:
            with pytest.raises(Retry):
                execute_pipeline.run(str(run.pk))
        finally:
            execute_pipeline.pop_request()
    run.refresh_from_db()
    assert run.state == "queued" and not run.output_id
    assert dataset.cells.count() == 1


def test_project_limits_are_shared_without_serializing_other_projects(settings):
    settings.CELERY_BROKER_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
    settings.WORKSHOP_PROJECT_CONCURRENCY = 2
    client = pipeline_runner._client(settings.CELERY_BROKER_URL)
    try:
        client.ping()
    except redis.ConnectionError:
        pytest.skip("Requires the test Redis service")
    first, second = uuid.uuid4().hex, uuid.uuid4().hex
    try:
        assert pipeline_runner.take_slot(first, "one")
        assert pipeline_runner.take_slot(first, "two")
        assert not pipeline_runner.take_slot(first, "three")
        assert pipeline_runner.take_slot(second, "other-project")
        pipeline_runner.release_slot(first, "unrelated-delivery")
        assert not pipeline_runner.take_slot(first, "three")
        pipeline_runner.release_slot(first, "one")
        assert pipeline_runner.take_slot(first, "three")
        client.zadd(f"workshop:project:{first}:slots", {"two": 0})
        assert pipeline_runner.take_slot(first, "after-expired-worker")
    finally:
        client.delete(f"workshop:project:{first}:slots", f"workshop:project:{second}:slots")
