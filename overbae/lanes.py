"""Celery worker lanes. Each lane is one hosted ECS service and one compose service.

Workers are resource profiles, queues are fairness classes. Only prefork enforces
time_limit and revoke(terminate=True), so every time-limited task routes to a prefork
lane. io_traces is a second queue on the io lane, not a second lane: round-robin keeps
an unbounded trace burst from queueing ahead of user-started eval scoring. Landing has
its own process: fair scheduling cannot preempt six occupied bulk slots.

The image owns each lane's command (`python -m overbae.lanes <lane>`); cloud-platform
owns its capacity. This module is stdlib-only because the deploy workflow reads it
without the app's dependencies.
"""

import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Lane:
    queues: tuple[str, ...]
    pool: str
    concurrency: int
    prefetch_multiplier: int | None = None
    # Reserve only what a free process can start, so queued work stays visible to
    # other consumers and to the demand metrics.
    disable_prefetch: bool = False


LANES = {
    "control": Lane(("control",), "threads", 8),
    "io": Lane(("io", "io_traces"), "threads", 24),
    "batch": Lane(("batch",), "prefork", 6, prefetch_multiplier=1, disable_prefetch=True),
    "landing": Lane(("landing",), "prefork", 1, prefetch_multiplier=1, disable_prefetch=True),
    "interactive": Lane(("interactive",), "prefork", 4, prefetch_multiplier=1),
}


def service(lane: str) -> str:
    return f"celery-{lane}-worker"


def lane_of(hostname: str) -> str | None:
    lane = hostname.partition("@")[0]
    return lane if lane in LANES else None


def argv(lane: str) -> list[str]:
    spec = LANES[lane]
    command = ["celery", "-A", "overbae", "worker", "-l", "info"]
    command += [f"--pool={spec.pool}", f"--concurrency={spec.concurrency}"]
    if spec.prefetch_multiplier is not None:
        command.append(f"--prefetch-multiplier={spec.prefetch_multiplier}")
    if spec.disable_prefetch:
        command.append("--disable-prefetch")
    # The health check addresses this exact consumer by name.
    command.append(f"--hostname={lane}@%h")
    return [*command, "-Q", ",".join(spec.queues)]


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in LANES:
        sys.exit(f"usage: python -m overbae.lanes {{{','.join(LANES)}}}")
    command = argv(sys.argv[1])
    os.execvp(command[0], command)
