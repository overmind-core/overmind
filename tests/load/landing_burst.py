import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest
import redis
import requests
from factories import make_project, make_user

from overbae.models import APIToken, Dataset, DatasetLandingWork
from tests.load.processes import landing_workers

pytestmark = pytest.mark.django_db(transaction=True)


def test_thousand_queued_imports_do_not_flood_the_broker_or_delay_another_project(
    process_api, settings, tmp_path
):
    user = make_user()
    noisy = make_project(member=user)
    quiet = make_project(member=user)
    key, _ = APIToken.create_for_user(user)
    headers = {"X-Api-Key": key}

    def submit(project, index):
        t = time.monotonic()
        r = requests.post(
            process_api.url + "/api/datasets/",
            headers=headers,
            json={
                "project": str(project.pk),
                "name": f"burst-{index}",
                "intent": "explore",
                "source": {"rows": [{"value": index}]},
            },
            timeout=60,
        )
        assert r.status_code == 201, r.text
        return {"id": r.json()["id"], "seconds": time.monotonic() - t}

    reads, stopped = [], threading.Event()

    def observe_reads():
        while not stopped.is_set():
            started = time.monotonic()
            response = requests.get(
                process_api.url + "/api/datasets/",
                params={"project": str(quiet.pk), "page_size": 25},
                headers=headers,
                timeout=10,
            )
            reads.append({"seconds": time.monotonic() - started, "status": response.status_code})
            stopped.wait(0.1)

    observer = threading.Thread(target=observe_reads)
    observer.start()
    try:
        with ThreadPoolExecutor(max_workers=32) as pool:
            submissions = list(pool.map(lambda i: submit(noisy, i), range(1000)))
    finally:
        stopped.set()
        observer.join(timeout=15)
    assert not observer.is_alive()
    assert reads and all(x["status"] == 200 for x in reads), reads
    latencies = sorted(x["seconds"] for x in reads)
    assert DatasetLandingWork.objects.filter(project=noisy).count() == 1000
    target = submit(quiet, 0)
    broker = redis.from_url(settings.CELERY_BROKER_URL)
    queued = broker.llen("landing")
    latency_summary = {
        "samples": len(reads),
        "p95_seconds": latencies[int(len(latencies) * 0.95)],
        "max_seconds": latencies[-1],
        "slowest_sample_index": max(range(len(reads)), key=lambda i: reads[i]["seconds"]),
    }
    report = {
        "requests": 1001,
        "concurrent_submitters": 32,
        "api_processes": process_api.workers,
        "queued_landing_messages": queued,
        "submissions": submissions,
        "quiet_page_reads_during_intake": reads,
        "quiet_page_latency": latency_summary,
    }
    Path("/tmp/overmind-landing-burst.json").write_text(json.dumps(report, indent=2))
    assert latencies[int(len(latencies) * 0.95)] < 0.5, latency_summary
    assert latencies[-1] < 2, latency_summary
    assert queued <= 2, report
    with landing_workers(settings, tmp_path):
        started = time.monotonic()
        while Dataset.objects.get(pk=target["id"]).state == "landing":
            assert time.monotonic() - started < 10
            time.sleep(0.05)
        result = Dataset.objects.get(pk=target["id"])
        assert result.state == "idle", result.error
        report["quiet_observed_after_workers_ready_seconds"] = time.monotonic() - started
        operation = result.operation
        report["quiet_operation"] = operation
        report["quiet_queue_seconds"] = (
            datetime.fromisoformat(operation["started_at"])
            - datetime.fromisoformat(operation["queued_at"])
        ).total_seconds()
        report["quiet_total_seconds"] = (
            datetime.fromisoformat(operation["finished_at"])
            - datetime.fromisoformat(operation["queued_at"])
        ).total_seconds()
        warm = submit(quiet, 1)
        started = time.monotonic()
        while Dataset.objects.get(pk=warm["id"]).state == "landing":
            assert time.monotonic() - started < 2
            time.sleep(0.02)
        warm_result = Dataset.objects.get(pk=warm["id"])
        assert warm_result.state == "idle", warm_result.error
        report["warm_quiet_total_seconds"] = warm["seconds"] + time.monotonic() - started
        assert DatasetLandingWork.objects.filter(project=noisy, state="waiting").exists()
        Path("/tmp/overmind-landing-burst.json").write_text(json.dumps(report, indent=2))
