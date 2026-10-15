from datetime import timedelta

import pytest
from django.utils import timezone

from overbae.models import DatasetPipelineRunner
from overbae.services.datasets import pipeline_runner, workbench
from sandbox.packages import runtime_id


@pytest.mark.django_db
def test_runtime_health_checks_identity_and_expires(settings):
    assert workbench.runner_status()["status"] == "unavailable"
    assert pipeline_runner.heartbeat() == "ready"
    assert workbench.runner_status()["images"] == [runtime_id()]
    DatasetPipelineRunner.objects.filter(pk="sandbox").update(
        heartbeat_at=timezone.now() - timedelta(seconds=46)
    )
    assert workbench.runner_status()["status"] == "unavailable"
    settings.SANDBOX_URL = "http://127.0.0.1:1"
    assert pipeline_runner.heartbeat() == "unavailable"
    assert workbench.runner_status()["status"] == "unavailable"
