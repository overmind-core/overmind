import multiprocessing
import os
import time

import redis

from overbae.tasks.utils.task_lock import acquire_task_lock, with_task_lock


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


def _hold_and_die(name: str) -> None:
    with acquire_task_lock(name, timeout=1, renew=True):
        os._exit(0)


def test_a_renewed_lock_holds_while_its_holder_runs_and_frees_soon_after_it_dies(settings):
    settings.CELERY_BROKER_URL = os.environ["TEST_REDIS_URL"]
    name = "journey_renewed_lock"
    redis.from_url(os.environ["TEST_REDIS_URL"]).delete(f"celery:lock:{name}")

    with acquire_task_lock(name, timeout=1, renew=True) as held:
        assert held
        time.sleep(2.5)
        with acquire_task_lock(name, timeout=1) as rival:
            assert not rival
    with acquire_task_lock(name, timeout=1) as after_release:
        assert after_release

    killed = multiprocessing.get_context("fork").Process(target=_hold_and_die, args=(name,))
    killed.start()
    killed.join(10)
    with acquire_task_lock(name, timeout=1) as while_dead_lease_runs:
        assert not while_dead_lease_runs
    time.sleep(1.5)
    with acquire_task_lock(name, timeout=1) as after_lease:
        assert after_lease
