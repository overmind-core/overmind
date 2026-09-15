from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest

from overbae.models import Capability, OptimizerExperiment, Project
from overbae.models.optimizer import run_experiment_advance

pytestmark = pytest.mark.django_db


@contextmanager
def _noop_lock(*_args, **_kwargs):
    """Stand-in for acquire_task_lock that skips Redis entirely."""
    yield True


def _experiment() -> OptimizerExperiment:
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="a", slug=f"a-{uuid.uuid4().hex[:6]}"
    )
    return OptimizerExperiment.objects.create(project=project, capability=capability)


def test_advance_failure_marks_experiment_failed(monkeypatch):
    monkeypatch.setattr("overbae.tasks.utils.task_lock.acquire_task_lock", _noop_lock)

    experiment = _experiment()

    def boom(self):
        raise RuntimeError("codegen exploded")

    monkeypatch.setattr(OptimizerExperiment, "advance", boom)
    run_experiment_advance(str(experiment.id))

    experiment.refresh_from_db()
    assert experiment.status == OptimizerExperiment.Status.FAILED
    assert experiment.failure_reason


def test_missing_experiment_is_a_noop(monkeypatch):
    monkeypatch.setattr("overbae.tasks.utils.task_lock.acquire_task_lock", _noop_lock)

    # A late retry after the row was deleted must not raise.
    run_experiment_advance(str(uuid.uuid4()))
