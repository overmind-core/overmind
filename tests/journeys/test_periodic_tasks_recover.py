import os

import redis

from overbae.tasks.utils.task_lock import with_task_lock


def test_a_task_lock_expires_on_its_timeout_and_releases_after_the_task(settings):
    settings.CELERY_BROKER_URL = os.environ["TEST_REDIS_URL"]
    client = redis.from_url(os.environ["TEST_REDIS_URL"])
    key = "celery:lock:journey_expiring_lock"
    client.delete(key)

    @with_task_lock(lock_name="journey_expiring_lock", timeout=7)
    def held_ttl():
        return client.ttl(key)

    assert 0 < held_ttl() <= 7
    assert client.ttl(key) == -2
