"""Queues exist only via ``CELERY_TASK_ROUTES`` (no ``CELERY_TASK_QUEUES``), so a worker
drains only the queues its lane names. A route no lane consumes is a silent no-op: the
task is accepted into a queue nobody reads. ``overbae.lanes`` is the one lane registry;
docker-compose, the hosted services and ``make worker`` all start from it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from django.conf import settings

from overbae.lanes import LANES, argv, service

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUEUE = settings.CELERY_TASK_DEFAULT_QUEUE


def _routed_queues() -> set[str]:
    routed = {route["queue"] for route in settings.CELERY_TASK_ROUTES.values()}
    return routed | {DEFAULT_QUEUE}


def _lane_queues(*, pool: str | None = None) -> set[str]:
    return {
        queue
        for lane in LANES.values()
        if pool is None or lane.pool == pool
        for queue in lane.queues
    }


def test_every_routed_queue_has_exactly_one_lane():
    consumed = [queue for lane in LANES.values() for queue in lane.queues]
    assert len(consumed) == len(set(consumed)), "Two lanes must not compete for one queue"
    orphaned = _routed_queues() - set(consumed)
    assert not orphaned, f"No lane consumes {sorted(orphaned)}; tasks sent there never run."


def test_time_limited_tasks_run_on_a_prefork_lane():
    """A threads pool silently ignores ``time_limit`` and ``soft_time_limit``, so a
    hung task pins one of its slots until the socket gives up."""
    from overbae.celery import app

    app.loader.import_default_modules()
    prefork = _lane_queues(pool="prefork")
    offenders = {
        name: queue
        for name, task in app.tasks.items()
        if name.startswith("overbae.")
        and (task.time_limit is not None or task.soft_time_limit is not None)
        and (queue := settings.CELERY_TASK_ROUTES.get(name, {}).get("queue", DEFAULT_QUEUE))
        not in prefork
    }
    assert not offenders, f"Time-limited tasks routed to a threads lane: {offenders}"


def test_landing_never_waits_behind_bulk_work():
    landing = LANES["landing"]
    assert set(landing.queues) == {
        settings.CELERY_TASK_ROUTES["overbae.tasks.datasets.land"]["queue"]
    }
    assert (
        settings.CELERY_TASK_ROUTES["overbae.tasks.eval.prepare_sample"]["queue"]
        not in landing.queues
    )
    assert landing.pool == "prefork" and landing.disable_prefetch


def test_lane_command_names_its_consumer_for_health_checks():
    command = argv("landing")
    assert command[:4] == ["celery", "-A", "overbae", "worker"]
    assert "--hostname=landing@%h" in command
    assert command[command.index("-Q") + 1] == "landing"


def test_docker_compose_runs_every_lane_once():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    started = {
        name: match.group(1)
        for name, item in compose["services"].items()
        if (match := re.search(r"python -m overbae\.lanes (\w+)$", str(item.get("command", ""))))
    }
    assert started == {service(lane): lane for lane in LANES}


def test_makefile_worker_drains_every_routed_queue():
    """One solo-pool process stands in for the whole compose worker fleet."""
    text = (REPO_ROOT / "Makefile").read_text()
    recipe = re.search(r"^worker:\n((?:\t.*\n?)+)", text, re.M).group(1).replace("\\\n", " ")
    consumed = set(re.search(r"-Q\s+([\w,]+)", recipe).group(1).split(","))
    assert _routed_queues() <= consumed, (
        f"`make worker` misses {sorted(_routed_queues() - consumed)}"
    )
