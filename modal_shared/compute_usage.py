import resource
import sys
import time
import uuid


def process_usage():
    try:
        values = [
            resource.getrusage(who) for who in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN)
        ]
        cpu = sum(value.ru_utime + value.ru_stime for value in values)
        memory = sum(value.ru_maxrss for value in values) * (
            1 if sys.platform == "darwin" else 1024
        )
        return cpu, memory
    except (OSError, ValueError):
        return None, None


class ComputeMeter:
    def __init__(self, *, gpu_type=None, gpu_count=0, cpu=0.125, memory_gib=0.125):
        self.started = time.monotonic()
        self.cpu_started, _ = process_usage()
        self.identity = uuid.uuid4().hex
        self.gpu_type, self.gpu_count = gpu_type, gpu_count
        self.cpu, self.memory_gib = cpu, memory_gib

    def snapshot(self):
        elapsed = max(0, time.monotonic() - self.started)
        cpu, memory = process_usage()
        # Modal omits cgroup mounts; reaped training/preparation children contribute through rusage.
        observed_cpu = (
            max(0, cpu - self.cpu_started) / 2
            if cpu is not None and self.cpu_started is not None
            else None
        )
        return {
            "usage_id": self.identity,
            "elapsed_seconds": elapsed,
            "gpu_type": self.gpu_type,
            "gpu_count": self.gpu_count,
            "cpu_core_seconds": max(self.cpu * elapsed, observed_cpu)
            if observed_cpu is not None
            else None,
            "memory_gib_seconds": max(self.memory_gib, memory / 1024**3) * elapsed
            if memory is not None
            else None,
            "basis": "cumulative worker window; CPU max(reserved, process plus finished-child CPU time/2), memory max(reserved, sum of process and finished-child peaks) times elapsed; estimates, not provider meters",
            "limitations": [
                "Running child resources enter the observation when reaped.",
                "Warm-container memory peaks can precede this call; summed peaks need not overlap.",
                "Unrelated container processes and provider billing adjustments are not measured.",
            ],
        }
