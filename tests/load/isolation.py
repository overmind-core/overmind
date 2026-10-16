import json
import time
import uuid
from pathlib import Path

import pytest
import requests
from factories import make_project, make_user
from journeys.surfaces import McpSurface

from overbae.models import APIToken, Dataset, DatasetPipelineRun
from overbae.services.datasets import pipeline_runner
from tests.workshop_script_fixtures import identity_script, retained_package, script_step

pytestmark = pytest.mark.django_db(transaction=True)


def test_slow_project_cannot_occupy_all_interactive_workers(live_api, profile_workers):
    pipeline_runner.heartbeat()
    identities = []
    for index in range(2):
        user = make_user()
        project = make_project(name=f"isolation-{index}", member=user)
        key, _ = APIToken.create_for_user(user, project=project)
        response = requests.post(
            live_api.url + "/api/datasets/",
            headers={"X-Api-Key": key},
            json={
                "project": str(project.pk),
                "name": "source",
                "intent": "explore",
                "source": {"rows": [{"value": 1}]},
            },
            timeout=10,
        )
        assert response.status_code == 201, response.text
        dataset = Dataset.objects.get(pk=response.json()["id"])
        deadline = time.monotonic() + 30
        while not dataset.active_cell and time.monotonic() < deadline:
            time.sleep(0.1)
            dataset.refresh_from_db()
        assert dataset.active_cell
        mcp = McpSurface(live_api.url, key)
        script = (
            identity_script()
            if index
            else script_step(
                "import time\ndef transform(rows, parameters):\n"
                "    time.sleep(8)\n    yield from rows\n"
            )
        )
        recipe = mcp.call(
            "save_dataset_pipeline",
            {
                "name": "isolation",
                "request_key": "recipe",
                "package": retained_package(project, user, [script]),
            },
        )["pipeline"]
        identities.append((project, dataset, mcp, recipe))

    def launch(index, *, fresh=False):
        _, dataset, mcp, recipe = identities[index]
        if fresh:
            response = requests.post(
                live_api.url + "/api/datasets/",
                headers={"X-Api-Key": mcp.api_key},
                json={
                    "project": str(identities[index][0].pk),
                    "name": "burst",
                    "intent": "explore",
                    "source": {"rows": [{"value": 1}]},
                },
                timeout=10,
            )
            assert response.status_code == 201, response.text
            dataset = Dataset.objects.get(pk=response.json()["id"])
            deadline = time.monotonic() + 20
            while not dataset.active_cell:
                assert time.monotonic() < deadline
                time.sleep(0.05)
                dataset.refresh_from_db()
        return mcp.call(
            "run_dataset_pipeline",
            {
                "dataset": str(dataset.pk),
                "pipeline": recipe["id"],
                "source_cell": str(dataset.active_cell.pk),
                "source_fingerprint": dataset.active_cell.fingerprint,
                "request_key": uuid.uuid4().hex,
            },
        )["run"]["id"]

    noisy = [launch(0, fresh=True) for _ in range(12)]
    deadline = time.monotonic() + 10
    while not DatasetPipelineRun.objects.filter(pk__in=noisy, state="running").exists():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    # Allow the broker to deliver the rest of the noisy project's burst.
    time.sleep(0.5)
    occupied = DatasetPipelineRun.objects.filter(pk__in=noisy, state="running").count()
    started = time.monotonic()
    quiet = launch(1)
    deadline = started + 45
    while DatasetPipelineRun.objects.get(pk=quiet).state not in ("completed", "failed"):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    elapsed = time.monotonic() - started
    result = {
        "noisy_running": occupied,
        "quiet_pipeline_seconds": elapsed,
        "interactive_workers": 4,
        "noisy_burst": len(noisy),
    }
    Path("/tmp/overmind-interactive-isolation.json").write_text(json.dumps(result, indent=2))
    for run_id in noisy:
        identities[0][2].call("cancel_dataset_pipeline_run", {"run": run_id})
    assert DatasetPipelineRun.objects.get(pk=quiet).state == "completed"
    assert elapsed < 5, result
