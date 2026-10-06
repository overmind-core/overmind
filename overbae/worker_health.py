"""Container health check: this task's own lane consumer is ready, or draining on purpose."""

import socket
import sys

from overbae.celery import app
from overbae.lanes import LANES
from overbae.worker_lifecycle import healthy


def check(lane: str) -> bool:
    destination = f"{lane}@{socket.gethostname()}"
    replies = app.control.broadcast("lane_health", destination=[destination], reply=True, timeout=5)
    reply = next((r[destination] for r in replies or [] if destination in r), None)
    return healthy(lane, reply)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in LANES:
        sys.exit(f"usage: python -m overbae.worker_health {{{','.join(LANES)}}}")
    try:
        ok = check(sys.argv[1])
    except Exception as exc:  # noqa: BLE001 — any probe failure is an unhealthy consumer
        print(f"{sys.argv[1]} consumer is not ready: {type(exc).__name__}", file=sys.stderr)
        ok = False
    raise SystemExit(0 if ok else 1)
