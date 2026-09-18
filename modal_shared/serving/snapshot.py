"""Snapshot-safe rendezvous for the pinned single-GPU serving stacks."""

import tempfile
import uuid
from pathlib import Path

from vllm.v1.executor.uniproc_executor import UniProcExecutor


class SnapshotExecutor(UniProcExecutor):
    def _distributed_args(self):
        if self.parallel_config.world_size != 1 or self.parallel_config.data_parallel_size != 1:
            raise RuntimeError("GPU snapshots require a single-rank serving worker")
        _, rank, local_rank = super()._distributed_args()
        # TCPStore's NCCL heartbeat retains a dead socket across restore. Each new
        # engine gets a nonexistent FileStore path that travels with its snapshot.
        path = Path(tempfile.gettempdir()) / f"vllm-rendezvous-{uuid.uuid4().hex}"
        return path.as_uri(), rank, local_rank
