from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

import uvicorn
from celery.contrib.testing.worker import start_worker
from celery.signals import before_task_publish, task_postrun
from django.conf import settings


class LiveAPI:
    def __init__(self) -> None:
        from overbae.asgi import application

        self._socket = socket.socket()
        self._socket.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self._socket.getsockname()[1]}"
        self._server = uvicorn.Server(
            uvicorn.Config(
                application,
                lifespan="on",
                log_level="warning",
                access_log=False,
                timeout_keep_alive=75,
            )
        )
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [self._socket]}, daemon=True
        )

    def start(self) -> LiveAPI:
        self._thread.start()
        deadline = time.monotonic() + 30
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("The live API did not start.")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def worker_queues() -> list[str]:
    routed = {route["queue"] for route in settings.CELERY_TASK_ROUTES.values()}
    return sorted(routed | {settings.CELERY_TASK_DEFAULT_QUEUE})


@dataclass
class TaskLedger:
    published: dict[str, str] = field(default_factory=dict)
    finished: dict[str, str] = field(default_factory=dict)
    scheduled: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def on_publish(self, headers=None, **_) -> None:
        if not headers:
            return
        with self._lock:
            target = self.scheduled if headers.get("eta") else self.published
            target[headers["id"]] = headers["task"]

    def on_finish(self, task_id=None, state=None, **_) -> None:
        with self._lock:
            self.finished[task_id] = state

    def pending(self) -> dict[str, str]:
        with self._lock:
            return {k: v for k, v in self.published.items() if k not in self.finished}

    def failed(self) -> dict[str, str]:
        with self._lock:
            return {
                task_id: self.published.get(task_id, "")
                for task_id, state in self.finished.items()
                if state not in ("SUCCESS", "IGNORED")
            }


@contextmanager
def celery_worker():
    from overbae.celery import app

    ledger = TaskLedger()
    before_task_publish.connect(ledger.on_publish, weak=False)
    task_postrun.connect(ledger.on_finish, weak=False)
    try:
        with start_worker(
            app,
            pool="threads",
            concurrency=4,
            queues=worker_queues(),
            perform_ping_check=False,
            shutdown_timeout=30,
        ):
            yield ledger
    finally:
        before_task_publish.disconnect(ledger.on_publish)
        task_postrun.disconnect(ledger.on_finish)


def drain(ledger: TaskLedger, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while ledger.pending():
        if time.monotonic() > deadline:
            raise TimeoutError(f"Tasks still running after {timeout}s: {ledger.pending()}")
        time.sleep(0.05)
