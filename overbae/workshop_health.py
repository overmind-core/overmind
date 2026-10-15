"""Read controller freshness and the approved images on its own Docker host."""

import sys
from datetime import timedelta

import django
from django.conf import settings
from django.utils import timezone


def healthy():
    django.setup()
    from overbae.models import DatasetPipelineRunner
    from overbae.services.datasets.pipeline_runner import DockerEngine

    images = settings.WORKSHOP_RUNTIME_IMAGES
    runner = DatasetPipelineRunner.objects.filter(
        pk="local",
        heartbeat_at__gte=timezone.now() - timedelta(seconds=30),
        status__in=["ready", "executing"],
    ).first()
    if not images or runner is None or set(runner.images) != set(images):
        return False
    engine = DockerEngine()
    return all(
        engine.request("GET", f"/images/{identity}/json", missing=True) for identity in images
    )


if __name__ == "__main__":
    try:
        ok = healthy()
    except Exception as exc:
        print(f"Workshop controller is not ready: {type(exc).__name__}", file=sys.stderr)
        ok = False
    raise SystemExit(0 if ok else 1)
