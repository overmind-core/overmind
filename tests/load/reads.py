import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
from factories import make_project, make_user

from overbae.models import APIToken

pytestmark = pytest.mark.django_db(transaction=True)


def test_concurrent_read_sessions_preserve_quiet_project_response(process_api, tmp_path):
    sessions = []
    for index in range(9):
        user = make_user()
        project = make_project(name=f"reader-{index}", member=user)
        key, _ = APIToken.create_for_user(user, project=project)
        sessions.append((str(project.pk), key))
    stop = threading.Event()
    report = {}
    errors = []
    completed = []

    def read(identity):
        project_id, key = identity
        started = time.monotonic()
        response = requests.get(
            f"{process_api.url}/api/projects/",
            headers={"X-Api-Key": key},
            timeout=5,
        )
        assert response.status_code == 200, response.text[:200]
        assert project_id in response.text
        return time.monotonic() - started

    def competing_session(index):
        count = 0
        try:
            while not stop.is_set():
                read(sessions[1 + index % 8])
                count += 1
                stop.wait(0.02)
        except Exception as exc:
            errors.append(repr(exc))
        finally:
            completed.append(count)

    def measure():
        samples = []
        for _ in range(40):
            samples.append(read(sessions[0]))
            time.sleep(0.01)
        ordered = sorted(samples)
        return {
            "samples": len(ordered),
            "api_processes": process_api.workers,
            "p50_seconds": ordered[19],
            "p95_seconds": ordered[math.ceil(len(ordered) * 0.95) - 1],
            "p99_seconds": ordered[-1],
        }

    report[0] = measure()
    for concurrency in (8, 32):
        stop.clear()
        completed.clear()
        try:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [
                    executor.submit(competing_session, index) for index in range(concurrency)
                ]
                try:
                    time.sleep(0.25)
                    report[concurrency] = measure()
                finally:
                    stop.set()
                for future in futures:
                    future.result(timeout=10)
            report[concurrency]["competing_requests"] = sum(completed)
            assert not errors, errors
            assert min(completed) > 0
            assert report[concurrency]["p95_seconds"] <= max(0.5, report[0]["p95_seconds"] * 2)
            assert report[concurrency]["p99_seconds"] < 1
        finally:
            artifact = tmp_path / "concurrent-read-latency.json"
            artifact.write_text(json.dumps(report, indent=2))
            print(f"READ_LOAD_REPORT={json.dumps(report)} ARTIFACT={artifact}")
