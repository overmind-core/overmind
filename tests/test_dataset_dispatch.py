"""Shared dataset creation for REST and MCP."""

from __future__ import annotations

import pytest
from factories import make_project, make_user

from overbae.models import Capability, Dataset
from overbae.services.datasets import dispatch, lifecycle
from overbae.services.datasets.lifecycle import DatasetError
from overbae.tasks.datasets import land as land_task

pytestmark = pytest.mark.django_db(transaction=True)

ROWS = [{"question": "q1", "answer": "a1"}]


def _dataset(project, *, state=Dataset.State.IDLE, name="ds") -> Dataset:
    return Dataset.objects.create(project=project, name=name, state=state)


def _queued(monkeypatch, task) -> list:
    calls: list = []
    monkeypatch.setattr(task, "apply_async", lambda kwargs, **_: calls.append(kwargs))
    return calls


def test_create_dataset_with_traces_lands_and_queues(monkeypatch):
    project, user = make_project(), make_user()
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


def test_a_deleted_capability_cannot_be_bound_on_any_path(monkeypatch):
    project, user = make_project(), make_user()
    _queued(monkeypatch, land_task)
    deleted = Capability.objects.create(
        project=project, name="Gone", slug="gone", status=Capability.Status.DELETED
    )
    source = {"traces": {"trace_ids": ["abc"]}}
    with pytest.raises(DatasetError):
        dispatch.create_dataset(
            project=project, user=user, name="ds", source=source, capability=deleted
        )
    dataset = _dataset(project)
    with pytest.raises(DatasetError):
        lifecycle.set_capability(dataset, deleted)
    assert Dataset.objects.filter(project=project, capability=deleted).count() == 0


def test_create_dataset_with_two_source_keys_raises():
    project, user = make_project(), make_user()
    with pytest.raises(DatasetError):
        dispatch.create_dataset(
            project=project,
            user=user,
            name="ds",
            source={"traces": {"trace_ids": ["a"]}, "rows": ROWS},
        )
