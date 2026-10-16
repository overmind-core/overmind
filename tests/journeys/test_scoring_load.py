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


@pytest.mark.parametrize("noisy_work", ["provider_wait", "database_and_storage"])
def test_quiet_project_latency_with_growing_backlog(
    settings, monkeypatch, tmp_path, noisy_work, live_api
):
    import json
    import math
    import time
    from contextlib import ExitStack

    import requests
    from django.db import close_old_connections
    from factories import make_member, make_user

    from journeys.surfaces import McpSurface
    from overbae.models import APIToken

    queue = f"test-latency-{uuid.uuid4().hex}"
    old_broker = app.conf.broker_url
    app.conf.broker_url = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
    quiet = Project.objects.create(name="quiet-latency", slug=uuid.uuid4().hex)
    noisy = Project.objects.create(name="noisy-latency", slug=uuid.uuid4().hex)
    user = make_user()
    make_member(user, quiet)
    raw_key, _ = APIToken.create_for_user(user, project=quiet)
    release = threading.Event()
    saturated = threading.Event()
    completed = {}
    lock = threading.Lock()
    occupied = 0
    pressure = {"file_bytes_written": 0, "database_reads": 0}

    def storage_pressure(key):
        payload = b"x" * (1024 * 1024)
        deadline = time.monotonic() + 60
        close_old_connections()
        try:
            with (tmp_path / key).open("w+b") as scratch:
                while not release.is_set() and time.monotonic() < deadline:
                    scratch.seek(0)
                    scratch.write(payload)
                    scratch.flush()
                    os.fsync(scratch.fileno())
                    scratch.seek(0)
                    assert scratch.read() == payload
                    LiveScoringWork.objects.filter(project=noisy, state="waiting").count()
                    with lock:
                        pressure["file_bytes_written"] += len(payload)
                        pressure["database_reads"] += 1
                    release.wait(0.02)
        finally:
            close_old_connections()

    async def score(ids):
        nonlocal occupied
        key = ids[0]
        if key.startswith("load-"):
            with lock:
                occupied += 1
                if occupied == 2:
                    saturated.set()
            if noisy_work == "database_and_storage":
                await asyncio.to_thread(storage_pressure, key)
            else:
                await asyncio.to_thread(release.wait, 60)
        else:
            # Keep provider time identical between baseline and loaded phases.
            await asyncio.sleep(0.01)
            with lock:
                completed[key] = time.monotonic()
        return []

    monkeypatch.setattr(innermind.span_processor, "score_spans", score)
    monkeypatch.setattr(innermind, "wake_live_scoring", lambda: None)
    settings.LIVE_SCORING_MAX_IN_FLIGHT = 4
    settings.LIVE_SCORING_MAX_PER_PROJECT = 2

    def dispatch():
        admission.dispatch(
            lambda pk, token: innermind.execute_live_scoring.apply_async(
                kwargs={"work_id": pk}, task_id=token, queue=queue
            )
        )

    def measure(backlog):
        durations = []
        rest_durations = []
        mcp_durations = []
        for index in range(30):
            key = f"quiet-{backlog}-{index}"
            started = time.monotonic()
            admission.enqueue(quiet.pk, "span", [key])
            dispatch()
            deadline = started + 5
            while time.monotonic() < deadline:
                with lock:
                    finished = completed.get(key)
                if finished is not None:
                    break
                time.sleep(0.005)
            else:
                pytest.fail(f"quiet request timed out with {backlog} competing records")
            durations.append(finished - started)
            read_started = time.monotonic()
            response = requests.get(
                f"{api.url}/api/projects/",
                headers={"X-Api-Key": raw_key},
                timeout=5,
            )
            assert response.status_code == 200, response.text[:200]
            rest_durations.append(time.monotonic() - read_started)
            read_started = time.monotonic()
            result = mcp.call("list_projects")
            assert str(quiet.pk) in json.dumps(result)
            mcp_durations.append(time.monotonic() - read_started)
        ordered = sorted(durations)
        return {
            "samples": len(ordered),
            "rest_p95_seconds": sorted(rest_durations)[math.ceil(len(ordered) * 0.95) - 1],
            "mcp_p95_seconds": sorted(mcp_durations)[math.ceil(len(ordered) * 0.95) - 1],
            **{
                f"p{percentile}_seconds": ordered[math.ceil(len(ordered) * percentile / 100) - 1]
                for percentile in (50, 95, 99)
            },
        }

    report = {}
    try:
        with ExitStack() as stack:
            api = live_api
            mcp = McpSurface(api.url, raw_key)
            for index in range(2):
                stack.enter_context(
                    start_worker(
                        app,
                        pool="threads",
                        use_eventloop=False,
                        concurrency=2,
                        queues=[queue],
                        hostname=f"latency-{index}-{uuid.uuid4().hex}",
                        perform_ping_check=False,
                        shutdown_timeout=15,
                    )
                )
            try:
                report[0] = measure(0)
                for backlog in (100, 10000):
                    previous = 0 if backlog == 100 else 100
                    admission.enqueue(
                        noisy.pk, "span", [f"load-{i}" for i in range(previous, backlog)]
                    )
                    dispatch()
                    assert saturated.wait(5)
                    report[backlog] = measure(backlog)
                    assert (
                        LiveScoringWork.objects.filter(
                            project=noisy, state__in=admission.ACTIVE
                        ).count()
                        == 2
                    )
                    assert (
                        LiveScoringWork.objects.filter(project=noisy, state="waiting").count()
                        == backlog - 2
                    )
                # A local integration budget, not a production latency SLA.
                baseline = report[0]["p95_seconds"]
                for backlog in (100, 10000):
                    assert report[backlog]["p95_seconds"] <= max(0.5, baseline * 2)
                    assert report[backlog]["p99_seconds"] < 1.0
                    for metric in ("rest_p95_seconds", "mcp_p95_seconds"):
                        assert report[backlog][metric] <= max(0.5, report[0][metric] * 2)
            finally:
                release.set()
                artifact = tmp_path / "quiet-project-latency.json"
                if noisy_work == "database_and_storage" and len(report) == 3:
                    assert pressure["database_reads"] > 100
                report["workload"] = noisy_work
                report["pressure"] = pressure
                artifact.write_text(json.dumps(report, indent=2))
                print(f"LATENCY_REPORT={json.dumps(report)} ARTIFACT={artifact}")
    finally:
        with app.connection_for_write() as connection:
            connection.channel().queue_delete(queue=queue)
        app.conf.broker_url = old_broker
