import io
import json
import os
import threading
import time
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import requests
from factories import make_project, make_user

from overbae.models import APIToken, Dataset
from tests.load.processes import landing_workers

pytestmark = pytest.mark.django_db(transaction=True)


def test_noisy_import_burst_leaves_a_landing_worker_for_another_project(
    live_api, settings, tmp_path
):
    targets = []
    source = os.environ.get("OVERMIND_LANDING_SOURCE")
    source_rows = pq.ParquetFile(source).metadata.num_rows if source else 8192
    for quiet in (False, True):
        user = make_user()
        project = make_project(member=user)
        key, _ = APIToken.create_for_user(user, project=project)
        headers = {"X-Api-Key": key}
        content = b"value\n" + b"".join(
            f"{row}-{'x' * 2048}\n".encode() for row in range(1 if quiet or source else 8192)
        )
        filename = Path(source).name if source and not quiet else "capacity.csv"
        size = Path(source).stat().st_size if source and not quiet else len(content)
        for index in range(1 if quiet else 4):
            reserved = requests.post(
                live_api.url + "/api/uploads/",
                headers=headers,
                json={"filename": filename},
                timeout=10,
            )
            assert reserved.status_code == 201, reserved.text
            upload_id = reserved.json()["upload_id"]
            with open(source, "rb") if source and not quiet else io.BytesIO(content) as stream:
                offset = 0
                while chunk := stream.read(8 * 1024**2):
                    response = requests.put(
                        f"{live_api.url}/api/uploads/{upload_id}/chunk/?offset={offset}",
                        headers=headers,
                        data=chunk,
                        timeout=30,
                    )
                    assert response.status_code == 200, response.text
                    offset += len(chunk)
            inspected = requests.post(
                f"{live_api.url}/api/uploads/{upload_id}/inspect/",
                headers=headers,
                json={"size": size},
                timeout=30,
            )
            assert inspected.status_code == 200, inspected.text
            response = requests.post(
                live_api.url + "/api/datasets/",
                headers=headers,
                json={
                    "project": str(project.pk),
                    "name": f"landing-{index}",
                    "intent": "explore",
                    "source": {"upload_id": upload_id, "filename": filename},
                },
                timeout=10,
            )
            assert response.status_code == 201, response.text
            targets.append((response.json()["id"], quiet))
    reads, stopped = [], threading.Event()

    def observe_reads():
        while not stopped.is_set():
            started = time.monotonic()
            try:
                response = requests.get(
                    live_api.url + "/api/datasets/",
                    params={"project": str(project.pk), "page_size": 25},
                    headers=headers,
                    timeout=10,
                )
                status = response.status_code
            except requests.RequestException:
                status = 0
            reads.append({"seconds": time.monotonic() - started, "status": status})
            stopped.wait(0.2)

    observer = threading.Thread(target=observe_reads)
    observer.start()
    warm_id = warm_started = warm_seconds = None
    try:
        with landing_workers(settings, tmp_path):
            deadline = time.monotonic() + int(
                os.environ.get("OVERMIND_LANDING_DEADLINE", "3600" if source else "90")
            )
            while Dataset.objects.filter(
                pk__in=[key for key, _ in targets], state="landing"
            ).exists():
                assert time.monotonic() < deadline
                if (
                    source
                    and warm_id is None
                    and Dataset.objects.get(pk=targets[-1][0]).state == "idle"
                ):
                    warm_started = time.monotonic()
                    response = requests.post(
                        live_api.url + "/api/datasets/",
                        headers=headers,
                        json={
                            "project": str(project.pk),
                            "name": "warm-small-import",
                            "intent": "explore",
                            "source": {"rows": [{"value": 1}]},
                        },
                        timeout=10,
                    )
                    assert response.status_code == 201, response.text
                    warm_id = response.json()["id"]
                    targets.append((warm_id, True))
                if (
                    warm_id
                    and warm_seconds is None
                    and Dataset.objects.get(pk=warm_id).state == "idle"
                ):
                    warm_seconds = time.monotonic() - warm_started
                time.sleep(0.1)
    finally:
        stopped.set()
        observer.join(timeout=15)
        Path("/tmp/overmind-landing-page-reads.json").write_text(
            json.dumps(
                {
                    "source_bytes": Path(source).stat().st_size if source else size,
                    "source_rows": source_rows,
                    "quiet_page_reads": reads,
                    "warm_quiet_import_seconds": warm_seconds,
                },
                indent=2,
            )
        )
    latencies = sorted(x["seconds"] for x in reads)
    report = {
        "source_bytes": size if not source else Path(source).stat().st_size,
        "source_rows": source_rows,
        "quiet_page_reads": reads,
        "warm_quiet_import_seconds": warm_seconds,
    }
    assert not observer.is_alive()
    assert reads and all(x["status"] == 200 for x in reads), report
    assert latencies[int(len(latencies) * 0.95)] < 0.5, report
    assert latencies[-1] < 1, report
    if source:
        assert warm_seconds is not None and warm_seconds < 2, report
    records = []
    for key, quiet in targets:
        dataset = Dataset.objects.get(pk=key)
        assert dataset.state == "idle", dataset.error
        assert dataset.source.rows == (1 if quiet else source_rows)
        records.append({"id": key, "quiet": quiet, **dataset.operation})
    noisy = sorted((x for x in records if not x["quiet"]), key=lambda x: x["started_at"])
    quiet = next(x for x in records if x["quiet"])
    Path("/tmp/overmind-landing-isolation.json").write_text(json.dumps(records, indent=2))
    assert quiet["started_at"] < noisy[1]["started_at"], records
    assert all(
        a["finished_at"] <= b["started_at"] for a, b in zip(noisy, noisy[1:], strict=False)
    ), records
