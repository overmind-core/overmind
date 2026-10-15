import os
import uuid

import pytest
import redis

from overbae.services.datasets import pipeline_runner


def test_project_limits_are_shared_without_serializing_other_projects(settings):
    settings.CELERY_BROKER_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
    settings.WORKSHOP_PROJECT_CONCURRENCY = 2
    client = pipeline_runner._client(settings.CELERY_BROKER_URL)
    try:
        client.ping()
    except redis.ConnectionError:
        pytest.skip("Requires the test Redis service")
    first, second = uuid.uuid4().hex, uuid.uuid4().hex
    try:
        assert pipeline_runner.take_slot(first, "one")
        assert pipeline_runner.take_slot(first, "two")
        assert not pipeline_runner.take_slot(first, "three")
        assert pipeline_runner.take_slot(second, "other-project")
        pipeline_runner.release_slot(first, "unrelated-delivery")
        assert not pipeline_runner.take_slot(first, "three")
        pipeline_runner.release_slot(first, "one")
        assert pipeline_runner.take_slot(first, "three")
        client.zadd(f"workshop:project:{first}:slots", {"two": 0})
        assert pipeline_runner.take_slot(first, "after-expired-worker")
    finally:
        client.delete(f"workshop:project:{first}:slots", f"workshop:project:{second}:slots")
