"""A worker restart leaves the Redis lock unreleased, so the per-lock safety timeout is
the only thing that revives the periodic task."""

import pytest


def _redis_or_skip():
    from overbae.tasks.utils.task_lock import _get_redis_client

    try:
        # Test settings use CELERY_BROKER_URL=memory:// which redis-py rejects.
        client = _get_redis_client()
        client.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Redis broker not reachable in this environment")
    return client


def test_lock_timeout_bounds_stale_holder_lifetime():
    from overbae.tasks.utils.task_lock import acquire_task_lock

    client = _redis_or_skip()
    key = "celery:lock:_test_timeout_lock"
    client.delete(key)

    with acquire_task_lock("_test_timeout_lock", timeout=5) as acquired:
        assert acquired
        ttl = client.ttl(key)
        # A killed holder can block re-acquisition for at most `timeout` seconds.
        assert 0 < ttl <= 5

    assert client.ttl(key) == -2  # released cleanly on exit


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
