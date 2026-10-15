from datetime import timedelta

import pytest
from django.utils import timezone

from overbae.models import DatasetPipelineRunner
from overbae.services.datasets.pipeline_runner import DockerEngine
from overbae.workshop_health import healthy


@pytest.mark.django_db
def test_controller_health_needs_fresh_receipt_and_local_images(settings, monkeypatch):
    image = "sha256:" + "a" * 64
    settings.WORKSHOP_RUNTIME_IMAGES = [image]
    monkeypatch.setattr(DockerEngine, "request", lambda *args, **kwargs: {"Id": image})
    assert not healthy()
    runner = DatasetPipelineRunner.objects.create(
        id="local", images=[image], status="ready", heartbeat_at=timezone.now()
    )
    assert healthy()
    runner.heartbeat_at = timezone.now() - timedelta(seconds=31)
    runner.save()
    assert not healthy()
    runner.heartbeat_at = timezone.now()
    runner.save()
    monkeypatch.setattr(DockerEngine, "request", lambda *args, **kwargs: None)
    assert not healthy()
