import resource
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from modal_shared.compute_usage import ComputeMeter


def test_worker_without_cgroups_includes_finished_child_resource_usage():
    counters = {
        resource.RUSAGE_SELF: SimpleNamespace(ru_utime=2, ru_stime=2, ru_maxrss=1024),
        resource.RUSAGE_CHILDREN: SimpleNamespace(ru_utime=10, ru_stime=2, ru_maxrss=2048),
    }
    with (
        patch("pathlib.Path.read_text", side_effect=FileNotFoundError),
        patch("resource.getrusage", side_effect=lambda who: counters[who]),
        patch("sys.platform", "linux"),
        patch("time.monotonic", side_effect=[100, 120]),
    ):
        meter = ComputeMeter(cpu=0.125, memory_gib=0.001)
        counters[resource.RUSAGE_CHILDREN] = SimpleNamespace(
            ru_utime=46, ru_stime=6, ru_maxrss=4096
        )
        value = meter.snapshot()
    assert value["cpu_core_seconds"] == pytest.approx(20)
    assert value["memory_gib_seconds"] == pytest.approx(5120 / 1024**2 * 20)
    assert value["elapsed_seconds"] == 20


def test_unavailable_resource_counters_stay_unknown():
    with (
        patch("pathlib.Path.read_text", side_effect=FileNotFoundError),
        patch("resource.getrusage", side_effect=OSError),
    ):
        value = ComputeMeter().snapshot()
    assert value["cpu_core_seconds"] is None
    assert value["memory_gib_seconds"] is None
