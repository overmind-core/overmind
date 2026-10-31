from __future__ import annotations

import uuid

import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from factories import auth_client

from overbae.models import (
    Capability,
    FinetuningJob,
    Project,
    ProjectMembership,
    User,
)

pytestmark = [pytest.mark.django_db, pytest.mark.covers("state-machines")]


def _setup():
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@test.com",
        password="x",
        clerk_user_id=f"c_{uuid.uuid4().hex}",
    )
    ProjectMembership.objects.create(user=user, project=project)
    dataset = frozen_dataset(project, TRAIN_ROWS, name="ds", contract="train")
    capability = Capability.objects.create(
        project=project,
        name="support-bot",
        slug="support-bot",
        model="openai/gpt-5.6-sol",
        source_path="capabilities/bot.py",
    )
    return project, user, dataset, capability


def _make_job(project, dataset, *, status=None, output_model_name="ft-model", capability=None):
    return FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        capability=capability,
        base_model="Qwen/Qwen2.5-14B-Instruct",
        baseline_model="openai/gpt-5.6-sol",
        status=status or FinetuningJob.Status.SUCCEEDED,
        output_model_name=output_model_name,
    )


def _get(client, job, *, pin=None):
    url = f"/api/finetuning-jobs/{job.id}/model-swap-prompt/"
    if pin is not None:
        return client.get(url, {"pin": pin})
    return client.get(url)


def test_model_swap_prompt_rejects_non_succeeded():
    project, user, dataset, capability = _setup()
    job = _make_job(project, dataset, status=FinetuningJob.Status.FAILED, capability=capability)

    resp = _get(auth_client(user), job)
    assert resp.status_code == 400
    assert "successfully" in resp.json()["detail"].lower()
