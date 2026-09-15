"""Queues exist only via ``CELERY_TASK_ROUTES`` (no ``CELERY_TASK_QUEUES``), so a worker
started without ``-Q`` drains ONLY ``CELERY_TASK_DEFAULT_QUEUE``. A route with no matching
worker is a silent no-op: the task is accepted into a queue nobody reads.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from django.conf import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUEUE = getattr(settings, "CELERY_TASK_DEFAULT_QUEUE", "celery")

_Q_FLAG = re.compile(r"-Q\s+([\w,]+)")
_IS_CELERY_WORKER = re.compile(r"\bcelery\b.*\bworker\b", re.S)


def _worker_queues(command: str) -> set[str] | None:
    """Queues drained, or None if not a worker. No ``-Q`` reports as {default queue},
    not "consumes everything" — that is the failure mode being guarded against."""
    if not _IS_CELERY_WORKER.search(command):
        return None
    match = _Q_FLAG.search(command)
    if not match:
        return {DEFAULT_QUEUE}
    return {q for q in match.group(1).split(",") if q}


def _routed_queues() -> set[str]:
    """Every queue named by CELERY_TASK_ROUTES, plus the implicit default."""
    routed = {
        route["queue"]
        for route in settings.CELERY_TASK_ROUTES.values()
        if isinstance(route, dict) and route.get("queue")
    }
    return routed | {DEFAULT_QUEUE}


def _compose_services() -> dict[str, dict]:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    return {
        name: service
        for name, service in (compose.get("services") or {}).items()
        if isinstance((service or {}).get("command"), str)
    }


def _compose_worker_queues() -> dict[str, set[str]]:
    workers = {}
    for name, service in _compose_services().items():
        queues = _worker_queues(service["command"])
        if queues is not None:
            workers[name] = queues
    return workers


def _makefile_worker_queues() -> set[str]:
    text = (REPO_ROOT / "Makefile").read_text()
    match = re.search(r"^worker:\n((?:\t.*\n?)+)", text, re.M)
    assert match, "Makefile no longer has a `worker:` target"
    recipe = match.group(1).replace("\\\n", " ")
    queues = _worker_queues(recipe)
    assert queues is not None, "Makefile `worker:` target no longer runs a celery worker"
    return queues


def test_docker_compose_drains_every_routed_queue():
    workers = _compose_worker_queues()
    assert workers, "no celery worker services found in docker-compose.yml"

    consumed = set().union(*workers.values())
    orphaned = _routed_queues() - consumed
    assert not orphaned, (
        f"queue(s) {sorted(orphaned)} are routed to in CELERY_TASK_ROUTES but no "
        f"docker-compose worker drains them — tasks sent there will silently never run. "
        f"Workers found: { {k: sorted(v) for k, v in workers.items()} }"
    )


def _prefork_queues() -> set[str]:
    """Queues drained by a pool that can enforce ``time_limit``."""
    drained: set[str] = set()
    for name, queues in _compose_worker_queues().items():
        if "--pool=prefork" in _compose_services()[name]["command"]:
            drained |= queues
    return drained


def test_time_limited_tasks_run_on_a_prefork_worker():
    """A threads pool silently ignores ``time_limit`` and ``soft_time_limit``, so a
    hung task pins one of its slots until the socket gives up. Declaring a limit and
    landing on a threads lane reads as protected and is not."""
    from overbae.celery import app

    app.loader.import_default_modules()
    prefork = _prefork_queues()
    offenders = {}
    for name, task in app.tasks.items():
        if not name.startswith("overbae."):
            continue
        if getattr(task, "time_limit", None) is None and (
            getattr(task, "soft_time_limit", None) is None
        ):
            continue
        route = settings.CELERY_TASK_ROUTES.get(name) or {}
        queue = route.get("queue", DEFAULT_QUEUE)
        if queue not in prefork:
            offenders[name] = queue
    assert not offenders, (
        f"time-limited task(s) routed to a non-prefork lane: {offenders}. "
        f"Prefork lanes: {sorted(prefork)}. Either route them to one or drop the limit."
    )


def test_makefile_worker_drains_every_routed_queue():
    """One solo-pool process stands in for the whole compose worker fleet, so it
    must subscribe to every queue explicitly."""
    consumed = _makefile_worker_queues()
    orphaned = _routed_queues() - consumed
    assert not orphaned, (
        f"`make worker` does not drain {sorted(orphaned)} — a dev running it would see "
        f"those tasks silently never execute. Add them to its -Q flag. Drains: {sorted(consumed)}"
    )
