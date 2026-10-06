"""A hosted worker must not be stopped while it owns work, and must not block scale-in,
deployments or rollbacks once it does not.

Failure modes this file owns:
- reserved work runs on an unprotected task, so scale-in or a deployment stops it;
- protection outlives the work, so scale-in and deployments wait forever;
- work longer than the protection lease loses protection halfway;
- a superseded busy worker keeps accepting work, so its deployment never finishes;
- a draining worker reports unhealthy, so ECS replaces it mid-task;
- a rolled-back deployment leaves a drained worker idle forever;
- an ECS outage or a missing permission crashes the worker or hides demand;
- local and test workers call ECS at all.
"""

import pytest
from celery.worker import state as worker_state
from fakes.ecs import NEXT_REVISION, OWN_REVISION

from overbae import ecs_task
from overbae.lanes import LANES
from overbae.worker_lifecycle import CHECK_SECONDS, RENEW_SECONDS, RETRY_SECONDS, Lifecycle, healthy


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Work:
    """Stands in for a Celery request the consumer has reserved."""


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def work():
    request = Work()
    yield request
    worker_state.reserved_requests.discard(request)


@pytest.fixture
def lifecycle(ecs, clock):
    events = []
    worker = Lifecycle(
        ecs_task.Task.current(),
        pause=lambda: events.append("pause"),
        resume=lambda: events.append("resume"),
        clock=clock,
    )
    worker.events = events
    return worker


def test_received_work_is_protected_until_the_worker_is_idle(ecs, lifecycle, work):
    worker_state.reserved_requests.add(work)
    lifecycle.received()
    assert ecs.protection == [True]

    lifecycle.tick()
    assert ecs.protection == [True]

    worker_state.reserved_requests.discard(work)
    lifecycle.tick()
    assert ecs.protection == [True, False]


def test_long_work_renews_protection_before_the_lease_ends(ecs, lifecycle, clock, work):
    worker_state.reserved_requests.add(work)
    lifecycle.tick()
    clock.now += RENEW_SECONDS + 1
    lifecycle.tick()
    assert ecs.protection == [True, True]


def test_superseded_worker_stops_taking_work_and_finishes_what_it_owns(ecs, lifecycle, clock, work):
    worker_state.reserved_requests.add(work)
    lifecycle.tick()
    ecs.primary_revision = NEXT_REVISION
    clock.now += CHECK_SECONDS
    lifecycle.tick()
    assert lifecycle.events == ["pause"]
    assert lifecycle.draining
    assert ecs.protection == [True]

    worker_state.reserved_requests.discard(work)
    clock.now += CHECK_SECONDS
    lifecycle.tick()
    assert lifecycle.events == ["pause"]
    assert ecs.protection == [True, False]


def test_rolled_back_deployment_puts_a_drained_worker_back_to_work(ecs, lifecycle, clock):
    ecs.primary_revision = NEXT_REVISION
    lifecycle.tick()
    ecs.primary_revision = OWN_REVISION
    clock.now += CHECK_SECONDS
    lifecycle.tick()
    assert lifecycle.events == ["pause", "resume"]
    assert not lifecycle.draining


def test_ecs_outage_keeps_the_worker_consuming_and_retries_protection(ecs, lifecycle, clock, work):
    ecs.agent_status = 500
    ecs.api_status = 500
    worker_state.reserved_requests.add(work)
    lifecycle.tick()
    assert lifecycle.events == []
    assert not lifecycle.draining
    assert ecs.protection == []

    ecs.agent_status = 200
    lifecycle.tick()
    assert ecs.protection == [], "a failed call backs off instead of retrying every tick"
    clock.now += RETRY_SECONDS
    lifecycle.tick()
    assert ecs.protection == [True]


def test_workers_outside_ecs_never_call_it(monkeypatch):
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    monkeypatch.delenv("ECS_AGENT_URI", raising=False)
    assert ecs_task.Task.current() is None


def test_health_requires_the_lane_consumer_unless_the_worker_is_draining():
    lane = LANES["landing"]
    consuming = {"queues": list(lane.queues), "draining": False, "missing": []}
    assert healthy("landing", consuming)
    assert not healthy("landing", None)
    assert not healthy("landing", {**consuming, "queues": []})
    assert not healthy("landing", {**consuming, "queues": [*lane.queues, "batch"]})
    assert not healthy("landing", {**consuming, "missing": ["overbae.tasks.datasets.land"]})
    assert healthy("landing", {**consuming, "queues": [], "draining": True})
