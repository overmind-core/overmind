"""Queues exist only via ``CELERY_TASK_ROUTES`` (no ``CELERY_TASK_QUEUES``), so a worker
started without ``-Q`` drains ONLY ``CELERY_TASK_DEFAULT_QUEUE``. A route with no matching
worker is a silent no-op: the task is accepted into a queue nobody reads.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
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


def test_packages_use_independently_scalable_interactive_capacity():
    from overbae.tasks.datasets import execute_pipeline

    assert settings.CELERY_TASK_ROUTES[execute_pipeline.name]["queue"] == "interactive"
    assert execute_pipeline.acks_late and execute_pipeline.reject_on_worker_lost
    services = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())["services"]
    assert "sandbox_net" in services["celery-interactive-worker"]["networks"]
    assert "workshop-runner" not in services


def test_fresh_worker_registers_every_routed_and_scheduled_task():
    # Test collection imports task modules that a fresh worker may never discover.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import django, json; django.setup(); "
            "from overbae.celery import app; app.loader.import_default_modules(); "
            "print(json.dumps(sorted(app.tasks)))",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    registered = set(json.loads(result.stdout.splitlines()[-1]))
    configured = set(settings.CELERY_TASK_ROUTES) | {
        entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()
    }
    missing = configured - registered
    assert not missing, f"Fresh workers cannot execute configured tasks: {sorted(missing)}"


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


def test_landing_has_dedicated_prefork_capacity():
    landing_queue = settings.CELERY_TASK_ROUTES["overbae.tasks.datasets.land"]["queue"]
    bulk_queue = settings.CELERY_TASK_ROUTES["overbae.tasks.innermind.generate_row"]["queue"]
    assert landing_queue != bulk_queue, "Evaluation fanout must not queue ahead of source landing"
    services = _compose_services()
    dedicated = {
        name: services[name]
        for name, queues in _compose_worker_queues().items()
        if queues == {landing_queue} and "--pool=prefork" in services[name]["command"]
    }
    assert dedicated, "Landing needs a process pool that never accepts bulk evaluation work"
    assert any(bulk_queue in queues for queues in _compose_worker_queues().values())


def test_training_preparation_does_not_wait_for_live_trace_scoring():
    from overbae.celery import app
    from overbae.tasks.innermind import score_spans
    from overbae.tasks.training_preparation import inspect_preparation

    preparation_queue = app.amqp.router.route({}, inspect_preparation.name)["queue"].name
    scoring_queue = app.amqp.router.route({}, score_spans.name)["queue"].name
    assert preparation_queue != scoring_queue
    assert preparation_queue in _makefile_worker_queues()
    for commands in (
        {name: service["command"] for name, service in _compose_services().items()},
        _deployed_commands(),
    ):
        io_workers = [
            command
            for command in commands.values()
            if preparation_queue in (_worker_queues(command) or set())
        ]
        trace_workers = [
            command
            for command in commands.values()
            if scoring_queue in (_worker_queues(command) or set())
        ]
        assert io_workers and trace_workers
        assert not set(io_workers) & set(trace_workers)
        assert all("--disable-prefetch" in command for command in [*io_workers, *trace_workers])


def test_new_producers_wait_for_healthy_landing_capacity():
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/deploy-api.yml").read_text())
    jobs = workflow["jobs"]

    def prerequisites(name):
        needs = jobs[name].get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        return set(needs).union(*(prerequisites(n) for n in needs))

    assert "deploy-landing" in jobs, "Provisioned landing consumers need a deployment gate"
    assert "migrate" in prerequisites("deploy-landing"), "New workers require the migrated schema"
    assert "deploy-landing" in prerequisites("deploy-api"), "Publishers must follow ready consumers"
    assert "deploy-landing" in prerequisites("deploy-workers")
    landing_steps = jobs["deploy-landing"]["steps"]
    assert any("deploy_ecs.py" in step.get("run", "") for step in landing_steps)
    assert any(
        step.get("env", {}).get("SERVICE") == "celery-landing-worker" for step in landing_steps
    )


def _deployed_commands() -> dict[str, str]:
    topology = json.loads((REPO_ROOT / "docker/worker-topology.json").read_text())
    return {name: " ".join(argv) for name, argv in topology.items()}


def test_prefork_lanes_reserve_only_the_tasks_they_run():
    """A reserved late-ack message stays unacknowledged while it waits behind a
    running task. Behind a 42-minute turn it outlives the broker's visibility
    timeout and is delivered twice; reserving only for a free slot bounds the
    unacknowledged time by the task's own time limit."""
    commands = {
        **{f"compose:{name}": service["command"] for name, service in _compose_services().items()},
        **{f"deploy:{name}": command for name, command in _deployed_commands().items()},
    }
    prefork = {name: c for name, c in commands.items() if "--pool=prefork" in c}
    assert prefork
    offenders = sorted(
        name
        for name, command in prefork.items()
        if "--prefetch-multiplier=1" not in command or "--disable-prefetch" not in command
    )
    assert not offenders, f"prefork workers that reserve ahead of a free slot: {offenders}"


def test_redis_redelivers_only_after_every_late_ack_task_has_hit_its_time_limit():
    """Redis hands an unacknowledged message to another worker after the
    visibility timeout, so a late-ack task still inside its time limit would run twice."""
    from overbae.celery import app

    app.loader.import_default_modules()
    visibility = settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"]
    offenders = {
        name: task.time_limit
        for name, task in app.tasks.items()
        if name.startswith("overbae.") and task.acks_late and (task.time_limit or 0) >= visibility
    }
    assert not offenders, (
        f"late-ack time limits at or above visibility_timeout={visibility}: {offenders}"
    )
