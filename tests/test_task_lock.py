"""A worker restart leaves the Redis lock unreleased, so the per-lock safety timeout is
the only thing that revives the periodic task."""

import multiprocessing
import os
import time
import uuid

import pytest

from overbae.tasks.utils.task_lock import acquire_task_lock


def _redis_or_skip():
    from overbae.tasks.utils.task_lock import _get_redis_client

    try:
        # Test settings use CELERY_BROKER_URL=memory:// which redis-py rejects.
        client = _get_redis_client()
        client.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Redis broker not reachable in this environment")
    return client


def test_with_task_lock_passes_timeout_through():
    from overbae.tasks.utils.task_lock import with_task_lock

    client = _redis_or_skip()
    key = "celery:lock:_test_decorator_lock"
    client.delete(key)

    @with_task_lock(lock_name="_test_decorator_lock", timeout=7)
    def observe_ttl():
        return client.ttl(key)

    ttl = observe_ttl()
    assert 0 < ttl <= 7
    assert client.ttl(key) == -2


@pytest.mark.skipif(
    not os.environ.get("WORKSHOP_REDIS_URL"),
    reason="Requires local Redis for process-loss lease verification",
)
def test_live_lease_renews_and_dead_collector_expires(settings):
    settings.CELERY_BROKER_URL = os.environ["WORKSHOP_REDIS_URL"]
    name = "workshop-lease-test-" + uuid.uuid4().hex
    context = multiprocessing.get_context("fork")
    ready = context.Event()

    def hold():
        with acquire_task_lock(name, timeout=2, renew=True) as acquired:
            assert acquired
            ready.set()
            time.sleep(30)

    process = context.Process(target=hold)
    process.start()
    try:
        assert ready.wait(5)
        time.sleep(3)
        with acquire_task_lock(name, timeout=2) as acquired:
            assert not acquired
        process.terminate()
        process.join(5)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            with acquire_task_lock(name, timeout=2) as acquired:
                if acquired:
                    break
            time.sleep(0.1)
        else:
            pytest.fail("The killed collector kept its lease beyond its recovery deadline")
    finally:
        if process.is_alive():
            process.terminate()
        process.join(5)
