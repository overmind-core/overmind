"""Shared source landing dispatch for REST and MCP."""

from __future__ import annotations

import uuid

import pytest

from overbae.models import Dataset, Project, User
from overbae.services.datasets import dispatch
from overbae.services.datasets.lifecycle import DatasetError
from overbae.tasks.datasets import land as land_task

pytestmark = pytest.mark.django_db(transaction=True)

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


def test_redelivered_landing_fails_both_split_datasets_without_reading_the_source():
    project = _project()
    train = _dataset(project, state=Dataset.State.LANDING)
    evaluation = _dataset(project, state=Dataset.State.LANDING)
    land_task.push_request(delivery_info={"redelivered": True})
    try:
        result = land_task.run(
            dataset_id=str(train.id),
            source={"rows": [{"question": "one"}, {"question": "two"}]},
            split={"eval_dataset_id": str(evaluation.id), "eval_percent": 50, "position": "head"},
        )
    finally:
        land_task.pop_request()
    assert result["status"] == "failed"
    for dataset in (train, evaluation):
        dataset.refresh_from_db()
        assert dataset.state == Dataset.State.ERROR
        assert "interrupted" in dataset.error
        assert dataset.source is None
