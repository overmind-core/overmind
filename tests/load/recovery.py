import json
import time
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone
from factories import make_project, make_user

from overbae.celery import app
from overbae.models import Dataset, DatasetPipelineRun
from overbae.services.datasets import land, pipeline_runner, workbench
from tests.load.processes import ProcessWorker
from tests.workshop_script_fixtures import identity_script, retained_package, script_step

pytestmark = pytest.mark.django_db(transaction=True)


def wait_until(condition, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError(f"condition did not hold within {timeout}s")


def test_prefork_loss_fences_script_and_other_project_recovers(settings, tmp_path):
    pipeline_runner.heartbeat()
    user = make_user()
    noisy = make_project(name="lost-worker", member=user)
    quiet = make_project(name="recovery-probe", member=user)

    def prepare(project, slow):
        dataset = Dataset.objects.create(project=project, name="source", intent="explore")
        land.land_rows(dataset, [{"value": 1}], user=user)
        dataset.refresh_from_db()
        step = (
            script_step(
                "import time\ndef transform(rows, parameters):\n"
                "    time.sleep(20)\n    yield from rows\n"
            )
            if slow
            else identity_script()
        )
        recipe = workbench.save_pipeline(
            project,
            user,
            name="recovery",
            request_key="recipe",
            package=retained_package(project, user, [step]),
        )
        return dataset, recipe

    def submit(dataset, recipe, key):
        return workbench.submit(
            dataset,
            user,
            pipeline=recipe.pk,
            source_cell=dataset.active_cell.pk,
            source_fingerprint=dataset.active_cell.fingerprint,
            request_key=key,
        )

    slow_dataset, slow_recipe = prepare(noisy, True)
    quiet_dataset, quiet_recipe = prepare(quiet, False)
    with ProcessWorker(settings, tmp_path, concurrency=2) as worker:
        slow = submit(slow_dataset, slow_recipe, "lost-attempt")
        wait_until(lambda: DatasetPipelineRun.objects.filter(pk=slow.pk, state="running").exists())
        active = app.control.inspect(destination=[worker.hostname], timeout=2).active()
        task = next(
            item for item in active[worker.hostname] if slow.pk.hex in str(item).replace("-", "")
        )
        worker.kill_task(task["worker_pid"])
        started = time.monotonic()
        probe = submit(quiet_dataset, quiet_recipe, "quiet-after-loss")
        wait_until(
            lambda: DatasetPipelineRun.objects.filter(pk=probe.pk, state="completed").exists()
        )
        elapsed = time.monotonic() - started
        assert elapsed < 10
        # Advance only the persisted deadline; no task or service is replaced.
        DatasetPipelineRun.objects.filter(pk=slow.pk).update(
            lease_until=timezone.now() - timedelta(seconds=1)
        )
        workbench.expire_runs()
        slow.refresh_from_db()
        assert slow.state == "failed"
        assert "No output was published" in slow.error
        assert slow_dataset.cells.count() == 1
        repeated = submit(slow_dataset, slow_recipe, "lost-attempt")
        assert repeated.pk == slow.pk and repeated.state == "failed"
        assert slow_dataset.cells.count() == 1
        Path("/tmp/overmind-worker-recovery.json").write_text(
            json.dumps(
                {
                    "quiet_after_worker_loss_seconds": elapsed,
                    "lost_attempt": str(slow.pk),
                    "lost_attempt_state": slow.state,
                    "same_key_replayed": False,
                    "source_cells": slow_dataset.cells.count(),
                },
                indent=2,
            )
        )
    print(f"RECOVERY_WORKER_LOG={worker.log_path}")
