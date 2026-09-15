from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from overbae.models import (
    Capability,
    Dataset,
    DeployedModel,
    EvalSet,
    EvalSetMember,
    Evaluator,
    FinetuningJob,
    Project,
    ProjectMembership,
    User,
)
from overbae.tasks.inference_controller import janitor_stuck_fsm
from overbae.tasks.model_deployment import base_model_slug

pytestmark = pytest.mark.django_db


def _job(*, status=FinetuningJob.Status.RUNNING, with_evals=True):
    from overbae.modal.model_registry import get_hf_base

    user = User.objects.create_user(
        email=f"janitor-{uuid.uuid4().hex[:8]}@example.com",
        password="pass",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
        projects_limit=5,
    )
    project = Project.objects.create(name="p", slug=f"p-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    capability = Capability.objects.create(
        project=project, name="a", slug=f"a-{uuid.uuid4().hex[:6]}"
    )
    train = Dataset.objects.create(project=project, name="train", intent="train")
    eval_ds = Dataset.objects.create(project=project, name="eval", intent="eval")
    eval_set = EvalSet.objects.create(project=project, capability=capability, name="set")
    ev = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="gate",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.FINAL_OUTPUT,
        config={"check": "exact_match"},
    )
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=ev, role=EvalSetMember.Role.GENERATIVE, order=0
    )
    job = FinetuningJob.objects.create(
        project=project,
        capability=capability,
        dataset=train,
        eval_dataset=eval_ds if with_evals else None,
        eval_set=eval_set if with_evals else None,
        base_model="Qwen/Qwen2.5-0.5B-Instruct",
        status=status,
        provider=FinetuningJob.Provider.MODAL,
        triggered_by=user,
    )
    slug = base_model_slug(get_hf_base(job.base_model))
    return job, slug


def test_janitor_fails_stuck_base_deploy_and_requeues_eval():
    job, slug = _job()
    DeployedModel.objects.create(
        project=job.project,
        finetuning_job=None,
        model_id=slug,
        base_model_id=job.base_model,
        status=DeployedModel.Status.WARMING,
        status_changed_at=timezone.now() - timedelta(minutes=91),
    )
    with patch("overbae.tasks.model_deployment.deploy_base_model_for_eval.delay") as delay:
        janitor_stuck_fsm()
    row = DeployedModel.objects.get(model_id=slug)
    assert row.status == DeployedModel.Status.FAILED
    delay.assert_called_once_with(job_id=str(job.id))


def test_janitor_does_not_requeue_stuck_finetune_deploy():
    job, _ = _job()
    DeployedModel.objects.create(
        project=job.project,
        finetuning_job=job,
        model_id=f"ft-{uuid.uuid4().hex[:8]}",
        base_model_id=job.base_model,
        status=DeployedModel.Status.WARMING,
        status_changed_at=timezone.now() - timedelta(minutes=91),
    )
    with patch("overbae.tasks.model_deployment.deploy_base_model_for_eval.delay") as delay:
        janitor_stuck_fsm()
    delay.assert_not_called()
    assert DeployedModel.objects.get(finetuning_job=job).status == DeployedModel.Status.FAILED
