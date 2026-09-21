"""A check never freezes a version; creating the consumer's row does, once."""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from conftest import EVAL_ROWS, TRAIN_ROWS, frozen_dataset
from django.utils import timezone

from overbae.api.serializers import FinetuningJobSerializer
from overbae.modal.model_registry import baseten_finetuning_catalog
from overbae.models import (
    Capability,
    Cell,
    Dataset,
    EvalSet,
    EvalSetMember,
    Evaluator,
    Project,
    ProjectMembership,
    User,
)
from overbae.models.optimizer import optimizer_dataset_error
from overbae.services.datasets import dispatch, lifecycle, use
from overbae.services.datasets.notebook import run as run_svc
from overbae.tasks import datasets as dataset_tasks

pytestmark = pytest.mark.django_db


def _setup():
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(project=project, name="Cap", slug="cap")
    user = User.objects.create_user(email=f"u-{uuid.uuid4().hex[:6]}@test.com", password="pw")
    ProjectMembership.objects.create(user=user, project=project)
    train = frozen_dataset(project, TRAIN_ROWS, capability=capability, contract="train")
    evaluation = frozen_dataset(project, EVAL_ROWS, capability=capability, contract="eval")
    return project, capability, user, train, evaluation


def _job(project, capability, user, train, evaluation, **fields):
    eval_set = EvalSet.objects.create(
        project=project, capability=capability, name="Test evaluations"
    )
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Exact match",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.FINAL_OUTPUT,
        config={"check": "exact_match"},
    )
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=evaluator, role=EvalSetMember.Role.GENERATIVE
    )
    return FinetuningJobSerializer(
        data={
            "project": str(project.id),
            "capability": str(capability.id),
            "dataset": str(train.id),
            "eval_dataset": str(evaluation.id),
            "eval_set": str(eval_set.id),
            "base_model": next(
                m["id"] for tier in baseten_finetuning_catalog().values() for m in tier
            ),
            "hyperparameters": {"training_type": {"type": "Lora"}},
            **fields,
        },
        context={"request": SimpleNamespace(user=user)},
    )


def _used(*datasets: Dataset) -> list[bool]:
    return [c.used_at is not None for d in datasets for c in Cell.objects.filter(dataset=d)]


def test_a_check_changes_nothing_and_a_use_freezes_once():
    _project, capability, _user, train, evaluation = _setup()
    assert use.check(train, "train") == train.active_cell
    assert optimizer_dataset_error(capability, evaluation) is None
    assert _used(train, evaluation) == [False, False]
    cell = use.use(train, "train")
    first = cell.used_at
    assert first is not None and use.use(train, "train").used_at == first


def test_a_check_refuses_the_wrong_intent_in_words():
    _project, _capability, _user, train, _evaluation = _setup()
    with pytest.raises(lifecycle.DatasetError, match="is a train dataset; this needs eval"):
        use.check(train, "eval")


def test_a_refused_training_launch_leaves_every_version_unused():
    setup = _setup()
    serializer = _job(*setup, base_model="not/a-model")
    assert not serializer.is_valid()
    assert "base_model" in serializer.errors
    assert _used(setup[3], setup[4]) == [False, False]


def test_a_training_job_pins_and_freezes_the_versions_it_was_checked_on():
    project, capability, user, train, evaluation = _setup()
    pinned = evaluation.active_cell
    later = lifecycle.add_cell(evaluation, title="later", script="df = df.head(1)\n")
    run_svc.execute(evaluation)
    evaluation.refresh_from_db()
    assert evaluation.active_cell == later
    serializer = _job(project, capability, user, train, evaluation, eval_cell=str(pinned.id))
    assert serializer.is_valid(), serializer.errors
    job = serializer.save(triggered_by=user)
    assert (job.cell, job.eval_cell) == (train.active_cell, pinned)
    later.refresh_from_db()
    assert _used(train) == [True] and job.eval_cell.used_at is not None
    assert later.used_at is None


def test_a_live_turn_on_an_old_dataset_is_not_reaped():
    project, _capability, user, train, _evaluation = _setup()
    Dataset.objects.filter(pk=train.pk).update(updated_at=timezone.now() - timedelta(days=2))
    dispatch.message_agent(train, user, "hello")
    assert dataset_tasks.reap_stuck_runs() == {"reaped": 0}
    train.refresh_from_db()
    assert train.state == Dataset.State.DIAGNOSING


def test_the_reaper_fails_a_state_only_after_that_state_own_limit():
    project, _capability, _user, train, evaluation = _setup()
    age = timedelta(seconds=dataset_tasks.RUN_HARD_LIMIT + dataset_tasks.REAP_GRACE + 60)
    Dataset.objects.filter(pk=train.pk).update(state="running", updated_at=timezone.now() - age)
    Dataset.objects.filter(pk=evaluation.pk).update(
        state="landing", updated_at=timezone.now() - age
    )
    assert dataset_tasks.reap_stuck_runs() == {"reaped": 1}
    train.refresh_from_db()
    evaluation.refresh_from_db()
    assert (train.state, evaluation.state) == ("error", "landing")
