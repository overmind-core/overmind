"""Shared create / chat / run dispatch for REST and MCP."""

from __future__ import annotations

import uuid

import pytest

from overbae.models import Dataset, Project, User
from overbae.services.datasets import dispatch, land, lifecycle
from overbae.services.datasets.lifecycle import DatasetError
from overbae.tasks.datasets import land as land_task
from overbae.tasks.datasets import run as run_task
from overbae.tasks.datasets import turn as turn_task

pytestmark = pytest.mark.django_db

ROWS = [{"question": "q1", "answer": "a1"}]


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _user() -> User:
    return User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )


def _dataset(project, *, state=Dataset.State.IDLE, name="ds") -> Dataset:
    return Dataset.objects.create(project=project, name=name, state=state)


def _queued(monkeypatch, task) -> list:
    calls: list = []
    monkeypatch.setattr(task, "apply_async", lambda kwargs, **_: calls.append(kwargs))
    return calls


def test_create_dataset_with_traces_lands_and_queues(monkeypatch):
    project, user = _project(), _user()
    queued = _queued(monkeypatch, land_task)
    source = {"traces": {"trace_ids": ["abc"]}}
    dataset = dispatch.create_dataset(project=project, user=user, name="from traces", source=source)
    assert dataset.state == Dataset.State.LANDING
    assert dataset.intent == Dataset.Intent.PENDING
    assert dataset.source_kind == Dataset.SourceKind.TRACES
    assert len(queued) == 1
    assert queued[0]["dataset_id"] == str(dataset.id)
    assert queued[0]["source"] == source
    assert queued[0]["user_id"] == str(user.id)


def test_create_dataset_with_two_source_keys_raises():
    project, user = _project(), _user()
    with pytest.raises(DatasetError):
        dispatch.create_dataset(
            project=project,
            user=user,
            name="ds",
            source={"traces": {"trace_ids": ["a"]}, "rows": ROWS},
        )


def test_message_agent_on_idle_becomes_diagnosing_and_queues_one_turn(monkeypatch):
    project, user = _project(), _user()
    dataset = _dataset(project)
    queued = _queued(monkeypatch, turn_task)
    dispatch.message_agent(dataset, user, "hi")
    dataset.refresh_from_db()
    assert dataset.state == Dataset.State.DIAGNOSING
    assert len(queued) == 1
    assert queued[0]["dataset_id"] == str(dataset.id)
    assert queued[0]["message"] == "hi"


@pytest.mark.parametrize(
    "state",
    [Dataset.State.RUNNING, Dataset.State.LANDING, Dataset.State.DIAGNOSING],
)
def test_message_agent_on_busy_raises_and_does_not_queue(monkeypatch, state):
    project, user = _project(), _user()
    dataset = _dataset(project, state=state)
    queued = _queued(monkeypatch, turn_task)
    with pytest.raises(DatasetError) as exc:
        dispatch.message_agent(dataset, user, "hi")
    assert exc.value.code == state
    assert queued == []


def test_message_agent_second_call_raises_atomically(monkeypatch):
    project, user = _project(), _user()
    dataset = _dataset(project)
    queued = _queued(monkeypatch, turn_task)
    dispatch.message_agent(dataset, user, "one")
    with pytest.raises(DatasetError) as exc:
        dispatch.message_agent(dataset, user, "two")
    assert exc.value.code == Dataset.State.DIAGNOSING
    assert len(queued) == 1


@pytest.mark.parametrize(
    "state",
    [Dataset.State.LANDING, Dataset.State.DIAGNOSING, Dataset.State.RUNNING],
)
def test_run_dataset_on_busy_raises(monkeypatch, state):
    project, user = _project(), _user()
    dataset = _dataset(project, state=state)
    queued = _queued(monkeypatch, run_task)
    with pytest.raises(DatasetError) as exc:
        dispatch.run_dataset(dataset, user)
    assert exc.value.code == state
    assert queued == []


@pytest.mark.parametrize("state", [Dataset.State.IDLE, Dataset.State.ERROR])
def test_run_dataset_on_idle_or_error_queues_and_is_running(monkeypatch, state):
    project, user = _project(), _user()
    dataset = _dataset(project, state=state)
    queued = _queued(monkeypatch, run_task)
    dispatch.run_dataset(dataset, user)
    dataset.refresh_from_db()
    assert dataset.state == Dataset.State.RUNNING
    assert len(queued) == 1
    assert queued[0]["dataset_id"] == str(dataset.id)


def test_run_dataset_refuses_when_db_busy_but_in_memory_idle(monkeypatch):
    project, user = _project(), _user()
    dataset = _dataset(project)
    land.land_rows(dataset, ROWS)
    proposal = lifecycle.add_cell(dataset, title="Keep", script="df = df\n", proposed=True)
    Dataset.objects.filter(pk=dataset.pk).update(state=Dataset.State.DIAGNOSING)
    dataset.state = Dataset.State.IDLE
    queued = _queued(monkeypatch, run_task)
    with pytest.raises(DatasetError) as exc:
        dispatch.run_dataset(dataset, user, proposal=proposal)
    assert exc.value.code == Dataset.State.DIAGNOSING
    assert queued == []
    proposal.refresh_from_db()
    assert proposal.state == "proposed"


def test_run_dataset_accepts_only_a_proposal_of_that_dataset(monkeypatch):
    project, user = _project(), _user()
    dataset = Dataset.objects.create(project=project, name="ds")
    land.land_rows(dataset, ROWS)
    dataset.refresh_from_db()
    proposal = lifecycle.add_cell(dataset, title="Keep", script="df = df\n", proposed=True)

    other = Dataset.objects.create(project=project, name="other")
    land.land_rows(other, ROWS)
    other.refresh_from_db()
    foreign = lifecycle.add_cell(other, title="Nope", script="df = df\n", proposed=True)

    queued = _queued(monkeypatch, run_task)
    with pytest.raises(DatasetError):
        dispatch.run_dataset(dataset, user, proposal=foreign)
    assert queued == []
    foreign.refresh_from_db()
    assert foreign.state == "proposed"

    dispatch.run_dataset(dataset, user, proposal=proposal)
    proposal.refresh_from_db()
    assert proposal.state == "queued"
    assert len(queued) == 1
    dataset.refresh_from_db()
    assert dataset.state == Dataset.State.RUNNING
