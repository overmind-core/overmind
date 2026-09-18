import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import modal_shared.serving


def test_snapshot_rendezvous_is_fresh_and_single_rank(monkeypatch):
    class Executor:
        def _distributed_args(self):
            return "tcp://unused", 0, 2

    monkeypatch.setitem(
        sys.modules,
        "vllm.v1.executor.uniproc_executor",
        SimpleNamespace(UniProcExecutor=Executor),
    )
    path = Path(modal_shared.serving.__file__).with_name("snapshot.py")
    spec = importlib.util.spec_from_file_location("snapshot_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    executor = module.SnapshotExecutor()
    executor.parallel_config = SimpleNamespace(world_size=1, data_parallel_size=1)
    first, rank, local_rank = executor._distributed_args()
    second, _, _ = executor._distributed_args()
    assert first.startswith("file:///") and first != second
    assert (rank, local_rank) == (0, 2)
    for config in (
        SimpleNamespace(world_size=2, data_parallel_size=1),
        SimpleNamespace(world_size=1, data_parallel_size=2),
    ):
        executor.parallel_config = config
        with pytest.raises(RuntimeError, match="single-rank"):
            executor._distributed_args()
