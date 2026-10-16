import asyncio
import os
import threading
import uuid

import pytest
from celery.contrib.testing.worker import start_worker

from overbae.celery import app
from overbae.innermind import admission
from overbae.models import LiveScoringWork, Project
from overbae.tasks import innermind

pytestmark = pytest.mark.django_db(transaction=True)


def test_trace_flood_leaves_worker_capacity_for_another_project_and_io(settings, monkeypatch):
    trace_queue = f"test-traces-{uuid.uuid4().hex}"
    io_queue = f"test-io-{uuid.uuid4().hex}"
    old_broker = app.conf.broker_url
    app.conf.broker_url = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
    noisy = Project.objects.create(name="noisy", slug=uuid.uuid4().hex)
    quiet = Project.objects.create(name="quiet", slug=uuid.uuid4().hex)
    admission.enqueue(noisy.pk, "span", [f"noisy-{i}" for i in range(10000)])
    admission.enqueue(quiet.pk, "span", ["quiet"])
    busy = threading.Event()
    release = threading.Event()
    quiet_done = threading.Event()
    io_done = threading.Event()
    slots = []

    async def score(ids):
        if ids[0].startswith("noisy-"):
            slots.append(ids[0])
            if len(slots) == 2:
                busy.set()
            await asyncio.to_thread(release.wait, 15)
        else:
            quiet_done.set()
        return []

    @app.task(name=f"tests.io_probe.{uuid.uuid4().hex}")
    def io_probe():
        io_done.set()

    monkeypatch.setattr(innermind.span_processor, "score_spans", score)
    monkeypatch.setattr(innermind, "wake_live_scoring", lambda: None)
    settings.LIVE_SCORING_MAX_IN_FLIGHT = 16
    settings.LIVE_SCORING_MAX_PER_PROJECT = 2
    try:
        with (
            start_worker(
                app,
                pool="threads",
                concurrency=4,
                queues=[trace_queue],
                hostname=f"traces-{uuid.uuid4().hex}",
                perform_ping_check=False,
            ),
            start_worker(
                app,
                pool="threads",
                concurrency=1,
                queues=[io_queue],
                hostname=f"io-{uuid.uuid4().hex}",
                perform_ping_check=False,
            ),
        ):
            try:
                admission.dispatch(
                    lambda pk, token: innermind.execute_live_scoring.apply_async(
                        kwargs={"work_id": pk}, task_id=token, queue=trace_queue
                    )
                )
                io_probe.apply_async(queue=io_queue)
                assert busy.wait(10), "noisy workload never occupied its two admitted slots"
                assert quiet_done.wait(5), "quiet project was starved by the flood"
                assert io_done.wait(5), "independent IO capacity was blocked by trace scoring"
                assert not release.is_set()
                assert len(slots) == 2
                assert (
                    LiveScoringWork.objects.filter(project=noisy, state="waiting").count() == 9998
                )
            finally:
                release.set()
    finally:
        with app.connection_for_write() as connection:
            channel = connection.channel()
            for queue in (trace_queue, io_queue):
                channel.queue_delete(queue=queue)
        app.conf.broker_url = old_broker
