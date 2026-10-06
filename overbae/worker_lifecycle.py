"""Keep a busy hosted worker out of ECS scale-in and deployment replacement.

ECS stops a task with a 120-second grace, shorter than an import, a workshop turn or
a generation. The worker protects its task while it holds reserved work and releases
it when idle, so scale-in removes only idle workers. When a newer revision becomes
primary it stops consuming and keeps protection until its own work finishes, so a
deployment completes without cutting work off. A rollback resumes consumption.
"""

import logging
import threading
import time

from celery import bootsteps
from celery.signals import task_received
from celery.worker import state as worker_state
from celery.worker.control import inspect_command

from overbae import ecs_task
from overbae.lanes import LANES, lane_of

logger = logging.getLogger(__name__)

TICK_SECONDS = 5
CHECK_SECONDS = 30
RENEW_SECONDS = ecs_task.PROTECTION_MINUTES * 60 // 2
RETRY_SECONDS = 60

lifecycle = None


class Lifecycle:
    def __init__(self, task, *, pause, resume, clock=time.monotonic):
        self.task = task
        self.draining = False
        self._pause = pause
        self._resume = resume
        self._clock = clock
        self._protected = False
        self._renew_at = 0.0
        self._retry_at = 0.0
        self._check_at = 0.0
        # The consumer signals receipts while the timer ticks in another thread.
        self._lock = threading.Lock()

    def received(self):
        with self._lock:
            if not self._protected:
                self._protect(True)

    def tick(self):
        with self._lock:
            now = self._clock()
            if now >= self._check_at:
                self._check_at = now + CHECK_SECONDS
                self._follow_deployment()
            busy = bool(worker_state.reserved_requests)
            if busy and (not self._protected or now >= self._renew_at):
                self._protect(True)
            elif not busy and self._protected:
                self._protect(False)

    def _follow_deployment(self):
        superseded = self.task.superseded()
        if superseded and not self.draining:
            logger.info("worker_lifecycle: a newer revision is primary; draining")
            self._pause()
            self.draining = True
        elif superseded is False and self.draining:
            logger.info("worker_lifecycle: this revision is primary again; consuming")
            self._resume()
            self.draining = False

    def _protect(self, enabled):
        now = self._clock()
        if now < self._retry_at:
            return
        if self.task.protect(enabled):
            self._protected = enabled
            self._renew_at = now + RENEW_SECONDS
        else:
            self._retry_at = now + RETRY_SECONDS


def healthy(lane, reply) -> bool:
    if not reply:
        return False
    # A draining worker has cancelled its consumer on purpose and still owns work.
    if reply["draining"]:
        return True
    return set(reply["queues"]) == set(LANES[lane].queues) and not reply["missing"]


@inspect_command()
def lane_health(state):
    consumer = state.consumer
    lane = lane_of(consumer.hostname)
    queues = [q.name for q in consumer.task_consumer.queues] if consumer.task_consumer else []
    routed = [
        name
        for name, route in consumer.app.conf.task_routes.items()
        if lane and route["queue"] in LANES[lane].queues
    ]
    return {
        "queues": queues,
        "draining": bool(lifecycle and lifecycle.draining),
        "missing": [name for name in routed if name not in consumer.app.tasks],
    }


@task_received.connect
def _protect_on_receipt(**_kwargs):
    if lifecycle:
        lifecycle.received()


class LaneLifecycle(bootsteps.StartStopStep):
    requires = ("celery.worker.consumer.tasks:Tasks",)

    def __init__(self, c, **kwargs):
        super().__init__(c, **kwargs)
        self.entry = None

    def start(self, c):
        global lifecycle
        lane = lane_of(c.hostname)
        if lane is None:
            return
        if lifecycle is None:
            try:
                task = ecs_task.Task.current()
            except (OSError, ValueError, KeyError) as exc:
                logger.warning("worker_lifecycle: ECS metadata unavailable: %s", exc)
                return
            if task is None:
                return

            # Remote control runs on the consumer's own loop, wherever the timer fires.
            def consume(enabled):
                for queue in LANES[lane].queues:
                    command = (
                        c.app.control.add_consumer if enabled else c.app.control.cancel_consumer
                    )
                    command(queue, destination=[c.hostname], reply=False)

            lifecycle = Lifecycle(task, pause=lambda: consume(False), resume=lambda: consume(True))
        self.entry = c.timer.call_repeatedly(TICK_SECONDS, lifecycle.tick)

    def stop(self, c):
        if self.entry is not None:
            self.entry.cancel()
            self.entry = None
