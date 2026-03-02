"""
Tests for tasks/utils/task_lock — distributed Celery task locking.

Covers:
  - acquire_task_lock yields True/False based on lock availability
  - Lock is always released on normal exit
  - Lock is released even when the body raises an exception
  - Lock key uses the celery:lock: prefix + the given name
  - with_task_lock decorator executes and returns the task result
  - with_task_lock returns a "skipped" dict when lock is unavailable
  - with_task_lock uses the function name as default lock_name
  - with_task_lock uses the provided lock_name when given
  - with_task_lock releases lock even if the task raises
  - wraps() preserves the wrapped function's __name__
"""

import pytest
from unittest.mock import MagicMock

from overmind.tasks.utils.task_lock import acquire_task_lock, with_task_lock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_lock():
    """A pre-configured mock Valkey lock that acquires successfully by default."""
    lock = MagicMock()
    lock.acquire.return_value = True
    return lock


@pytest.fixture()
def mock_valkey_client(mock_lock):
    """A mock Valkey client that returns mock_lock for any .lock() call."""
    client = MagicMock()
    client.lock.return_value = mock_lock
    return client


@pytest.fixture(autouse=True)
def patch_valkey(monkeypatch, mock_valkey_client):
    """Replace get_valkey_client globally for every test in this module."""
    monkeypatch.setattr(
        "overmind.tasks.utils.task_lock.get_valkey_client",
        lambda: mock_valkey_client,
    )


# ---------------------------------------------------------------------------
# acquire_task_lock — context manager
# ---------------------------------------------------------------------------


class TestAcquireTaskLock:
    def test_yields_true_when_lock_acquired(self, mock_lock):
        mock_lock.acquire.return_value = True
        with acquire_task_lock("my_task") as acquired:
            assert acquired is True

    def test_yields_false_when_lock_unavailable(self, mock_lock):
        mock_lock.acquire.return_value = False
        with acquire_task_lock("my_task") as acquired:
            assert acquired is False

    def test_releases_lock_after_normal_exit(self, mock_lock):
        mock_lock.acquire.return_value = True
        with acquire_task_lock("my_task"):
            pass
        mock_lock.release.assert_called_once()

    def test_does_not_release_when_not_acquired(self, mock_lock):
        mock_lock.acquire.return_value = False
        with acquire_task_lock("my_task"):
            pass
        mock_lock.release.assert_not_called()

    def test_releases_lock_on_exception(self, mock_lock):
        mock_lock.acquire.return_value = True
        with pytest.raises(RuntimeError, match="boom"):
            with acquire_task_lock("my_task"):
                raise RuntimeError("boom")
        mock_lock.release.assert_called_once()

    def test_lock_key_uses_celery_prefix(self, mock_valkey_client):
        with acquire_task_lock("agent_discovery"):
            pass
        key_arg = mock_valkey_client.lock.call_args[0][0]
        assert key_arg == "celery:lock:agent_discovery"

    def test_lock_key_includes_custom_name(self, mock_valkey_client):
        with acquire_task_lock("my_custom_task"):
            pass
        key_arg = mock_valkey_client.lock.call_args[0][0]
        assert "my_custom_task" in key_arg

    def test_lock_has_safety_timeout(self, mock_valkey_client):
        with acquire_task_lock("some_task"):
            pass
        kwargs = mock_valkey_client.lock.call_args[1]
        assert "timeout" in kwargs
        # Safety timeout should be at least 1 day (86400 s)
        assert kwargs["timeout"] >= 86400

    def test_acquire_called_with_non_blocking_by_default(self, mock_lock):
        with acquire_task_lock("some_task"):
            pass
        mock_lock.acquire.assert_called_once_with(blocking=False)

    def test_acquire_called_with_blocking_when_requested(self, mock_lock):
        with acquire_task_lock("some_task", blocking=True):
            pass
        mock_lock.acquire.assert_called_once_with(blocking=True)

    def test_release_error_is_suppressed(self, mock_lock):
        """A failure during lock.release() must not propagate."""
        mock_lock.acquire.return_value = True
        mock_lock.release.side_effect = Exception("valkey down")
        # Should not raise
        with acquire_task_lock("some_task"):
            pass


# ---------------------------------------------------------------------------
# with_task_lock — decorator
# ---------------------------------------------------------------------------


class TestWithTaskLock:
    def test_executes_task_when_lock_acquired(self, mock_lock):
        mock_lock.acquire.return_value = True

        @with_task_lock(lock_name="test_lock")
        def my_task():
            return {"status": "done", "count": 42}

        result = my_task()
        assert result == {"status": "done", "count": 42}

    def test_returns_skipped_dict_when_lock_unavailable(self, mock_lock):
        mock_lock.acquire.return_value = False

        @with_task_lock(lock_name="test_lock")
        def my_task():
            return {"status": "done"}

        result = my_task()
        assert result["status"] == "skipped"
        assert result["reason"] == "previous_task_still_running"
        assert "test_lock" in result["message"]

    def test_task_body_not_called_when_lock_unavailable(self, mock_lock):
        mock_lock.acquire.return_value = False
        spy = MagicMock(return_value={})

        @with_task_lock(lock_name="test_lock")
        def my_task():
            return spy()

        my_task()
        spy.assert_not_called()

    def test_uses_function_name_as_default_lock_name(
        self, mock_valkey_client, mock_lock
    ):
        mock_lock.acquire.return_value = True

        @with_task_lock()
        def periodic_review_task():
            return {}

        periodic_review_task()
        key_arg = mock_valkey_client.lock.call_args[0][0]
        assert "periodic_review_task" in key_arg

    def test_uses_provided_lock_name_over_function_name(
        self, mock_valkey_client, mock_lock
    ):
        mock_lock.acquire.return_value = True

        @with_task_lock(lock_name="explicit_lock")
        def my_function():
            return {}

        my_function()
        key_arg = mock_valkey_client.lock.call_args[0][0]
        assert "explicit_lock" in key_arg
        assert "my_function" not in key_arg

    def test_releases_lock_after_task_completes(self, mock_lock):
        mock_lock.acquire.return_value = True

        @with_task_lock(lock_name="test_lock")
        def my_task():
            return {}

        my_task()
        mock_lock.release.assert_called_once()

    def test_releases_lock_when_task_raises(self, mock_lock):
        mock_lock.acquire.return_value = True

        @with_task_lock(lock_name="test_lock")
        def failing_task():
            raise ValueError("task error")

        with pytest.raises(ValueError, match="task error"):
            failing_task()
        mock_lock.release.assert_called_once()

    def test_does_not_release_lock_when_skipped(self, mock_lock):
        mock_lock.acquire.return_value = False

        @with_task_lock(lock_name="test_lock")
        def my_task():
            return {}

        my_task()
        mock_lock.release.assert_not_called()

    def test_passes_args_and_kwargs_to_task(self, mock_lock):
        mock_lock.acquire.return_value = True
        received = {}

        @with_task_lock(lock_name="test_lock")
        def my_task(a, b, c=None):
            received.update({"a": a, "b": b, "c": c})
            return received

        my_task(1, 2, c="three")
        assert received == {"a": 1, "b": 2, "c": "three"}

    def test_preserves_function_name_via_wraps(self):
        @with_task_lock(lock_name="test_lock")
        def uniquely_named_function():
            return {}

        assert uniquely_named_function.__name__ == "uniquely_named_function"

    def test_skipped_message_contains_lock_name(self, mock_lock):
        mock_lock.acquire.return_value = False

        @with_task_lock(lock_name="my_special_task")
        def my_task():
            return {}

        result = my_task()
        assert "my_special_task" in result["message"]

    def test_multiple_concurrent_tasks_only_one_runs(self, mock_lock):
        """Simulate two calls: first acquires, second does not."""
        call_count = 0

        @with_task_lock(lock_name="test_lock")
        def my_task():
            nonlocal call_count
            call_count += 1
            return {"call_count": call_count}

        mock_lock.acquire.return_value = True
        result1 = my_task()
        assert result1["call_count"] == 1

        mock_lock.acquire.return_value = False
        result2 = my_task()
        assert result2["status"] == "skipped"
        assert call_count == 1  # body only ran once
