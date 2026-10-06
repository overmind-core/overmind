"""Queue waiting must not consume an agent's execution lease or disappear from demand."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from overbae.models import Dataset, Project
from overbae.services.datasets import dispatch, lifecycle
from overbae.services.queue_capacity import read_workloads
from overbae.tasks import datasets as tasks

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def queued(monkeypatch):
    project = Project.objects.create(name="Workshop capacity", slug=uuid.uuid4().hex)
    dataset = Dataset.objects.create(project=project, state="idle")
    messages = []
    monkeypatch.setattr(tasks.turn, "apply_async", lambda **kw: messages.append(kw))
    dispatch.message_agent(dataset, None, "Inspect the source")
    dataset.refresh_from_db()
    return dataset, messages[0]["task_id"]


@pytest.mark.parametrize("state,minutes", [("diagnosing", 48), ("running", 28)])
def test_waiting_does_not_use_execution_deadline(queued, state, minutes):
    dataset, task_id = queued
    queued_at = timezone.now() - timedelta(minutes=minutes)
    Dataset.objects.filter(pk=dataset.pk).update(
        state=state, updated_at=queued_at, workshop_queued_at=queued_at
    )
    assert tasks.reap_stuck_runs()["reaped"] == 0
    assert lifecycle.claim_workshop(dataset.pk, task_id, state=state)
    dataset.refresh_from_db()
    assert dataset.workshop_started_at > queued_at + timedelta(minutes=minutes - 1)
    assert not lifecycle.claim_workshop(dataset.pk, task_id, state=state)


def test_queue_expiry_is_distinct_and_late_delivery_cannot_start(queued):
    dataset, task_id = queued
    old = timezone.now() - timedelta(minutes=61)
    Dataset.objects.filter(pk=dataset.pk).update(workshop_queued_at=old, updated_at=old)
    assert tasks.reap_stuck_runs()["reaped"] == 1
    dataset.refresh_from_db()
    assert dataset.state == "error"
    assert "waited too long" in dataset.error
    assert not lifecycle.claim_workshop(dataset.pk, task_id, state="diagnosing")


def test_progress_does_not_extend_absolute_execution_deadline(queued):
    dataset, task_id = queued
    assert lifecycle.claim_workshop(dataset.pk, task_id, state="diagnosing")
    Dataset.objects.filter(pk=dataset.pk).update(
        workshop_started_at=timezone.now() - timedelta(minutes=48), updated_at=timezone.now()
    )
    assert tasks.reap_stuck_runs()["reaped"] == 1
    dataset.refresh_from_db()
    assert "worker stopped" in dataset.error


def test_wrong_task_cannot_claim_or_hide_queued_demand(queued):
    dataset, task_id = queued
    old = timezone.now() - timedelta(minutes=4)
    Dataset.objects.filter(pk=dataset.pk).update(workshop_queued_at=old)
    assert not lifecycle.claim_workshop(dataset.pk, str(uuid.uuid4()), state="diagnosing")
    demand = read_workloads()["interactive"]
    assert (demand["waiting"], demand["running"], demand["oldest"]) == (1, 0, old)
    assert lifecycle.claim_workshop(dataset.pk, task_id, state="diagnosing")
    demand = read_workloads()["interactive"]
    assert (demand["waiting"], demand["running"], demand["oldest"]) == (0, 1, None)
    Dataset.objects.filter(pk=dataset.pk).update(state="idle")
    assert read_workloads()["interactive"]["running"] == 0


def test_workshop_scaling_tracks_slots_and_preserves_busy_workers():
    from scripts.plan_workshop_capacity import capacity_plan

    plan = capacity_plan(cluster="test", min_capacity=1, max_capacity=6)
    target = plan["scalable-target.json"]
    assert target["MaxCapacity"] == 6
    assert target["SuspendedState"]["DynamicScalingInSuspended"] is True
    policy = plan["backlog-policy.json"]["TargetTrackingScalingPolicyConfiguration"]
    assert policy["TargetValue"] == 4
    assert policy["DisableScaleIn"] is True
    assert policy["CustomizedMetricSpecification"]["MetricName"] == "BacklogPerWorker"
    assert any(a["MetricName"] == "OldestQueuedAgeSeconds" for a in plan["alarms.json"])


def test_stale_settlement_cannot_finish_a_successor(queued):
    from overbae.services.datasets.notebook import agent

    dataset, previous = queued
    Dataset.objects.filter(pk=dataset.pk).update(state="idle")
    dispatch.message_agent(dataset, None, "A new request")
    dataset.refresh_from_db()
    successor = dataset.workshop_task_id
    agent.settle(dataset.pk, turn_key=previous)
    dataset.refresh_from_db()
    assert dataset.state == "diagnosing"
    assert dataset.workshop_task_id == successor
    assert lifecycle.claim_workshop(dataset.pk, successor, state="diagnosing")
    agent.settle(dataset.pk, turn_key=successor)
    dataset.refresh_from_db()
    assert dataset.state == "idle"


def test_receiptless_diagnosis_still_requires_current_ownership(queued):
    from overbae.services.datasets import imports

    dataset, task_id = queued
    assert not imports.claim_diagnosis(dataset.pk, str(uuid.uuid4()))
    Dataset.objects.filter(pk=dataset.pk).update(state="error")
    assert not imports.claim_diagnosis(dataset.pk, task_id)
