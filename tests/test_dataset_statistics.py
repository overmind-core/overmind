import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from overbae.services.datasets import store


def test_concurrent_statistics_are_isolated_cached_and_labelled(tmp_path):
    path = tmp_path / "rows.parquet"
    store.write_rows(
        path, ({"text": "long unique evidence " * 100 + str(i), "n": i} for i in range(12_001))
    )
    with (
        patch.object(store, "connect", side_effect=AssertionError("statistics in request process")),
        ThreadPoolExecutor(max_workers=3) as executor,
    ):
        results = list(
            executor.map(lambda _: store.column_stats(path, fingerprint="fixed"), range(3))
        )
    assert results[0] == results[1] == results[2]
    for entry in results[0]:
        assert entry["total_rows"] == 12_001
        assert entry["sample_rows"] == 10_000
        assert entry["approximate"] is True
        assert entry["method"] == "deterministic_reservoir"
    with patch("subprocess.run", side_effect=AssertionError("cached statistics recomputed")):
        assert store.column_stats(path, fingerprint="fixed") == results[0]
    store.write_rows(path, [{"text": "replacement", "n": 7}])
    result = store.column_stats(path, fingerprint="replacement")
    assert result[0]["total_rows"] == 1 and result[0]["approximate"] is False
    assert result[1]["min"] == 7


def test_failed_statistics_child_leaves_no_success_cache(tmp_path):
    path = tmp_path / "rows.parquet"
    store.write_rows(path, [{"x": 1}])
    with (
        patch("subprocess.run", side_effect=TimeoutError("worker stalled")),
        pytest.raises(store.StoreError, match="statistics"),
    ):
        store.column_stats(path, fingerprint="fixed")
    assert store.column_stats(path, fingerprint="fixed")[0]["distinct"] == 1


def test_killed_statistics_child_does_not_kill_parent_or_poison_cache(tmp_path):
    path = tmp_path / "rows.parquet"
    store.write_rows(path, [{"x": 7}])
    run = subprocess.run

    def killed_child(*args, **kwargs):
        return run(
            [sys.executable, "-c", "import os,signal; os.kill(os.getpid(), signal.SIGKILL)"],
            check=True,
            capture_output=True,
            timeout=5,
        )

    with (
        patch("subprocess.run", side_effect=killed_child),
        pytest.raises(store.StoreError, match="statistics") as error,
    ):
        store.column_stats(path, fingerprint="fixed")
    assert error.value.__cause__.__cause__.returncode == -signal.SIGKILL
    assert not list((tmp_path / ".statistics").glob("*.json"))
    assert store.column_stats(path, fingerprint="fixed")[0]["min"] == 7
