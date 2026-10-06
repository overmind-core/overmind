"""Fail a worker container health check unless its own Celery consumer is ready."""

from __future__ import annotations

import socket
import sys

import django

from overbae.celery import app

QUEUE_TASKS = {
    "landing": "overbae.tasks.datasets.land",
    "interactive": "overbae.tasks.datasets.turn",
}


def healthy(queue):
    django.setup()

    destination = f"{queue}@{socket.gethostname()}"
    inspector = app.control.inspect(destination=[destination], timeout=5)
    queues = (inspector.active_queues() or {}).get(destination, [])
    if {entry["name"] for entry in queues} != {queue}:
        return False
    registered = (inspector.registered() or {}).get(destination, [])
    return QUEUE_TASKS[queue] in registered


if __name__ == "__main__":
    try:
        ok = healthy(sys.argv[1])
    except Exception as exc:
        print(f"Worker consumer is not ready: {type(exc).__name__}", file=sys.stderr)
        ok = False
    raise SystemExit(0 if ok else 1)
