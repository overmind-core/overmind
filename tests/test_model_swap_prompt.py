from __future__ import annotations

import uuid

import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from rest_framework.test import APIClient

from overbae.models import (
    Capability,
    DeployedModel,
    FinetuningJob,
    Project,
    ProjectMembership,
    User,
)
from overbae.services.model_swap_prompt import model_swap_prompt_for_job

pytestmark = pytest.mark.django_db


def _setup():
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@test.com",
        password="x",
        clerk_user_id=f"c_{uuid.uuid4().hex}",
        projects_limit=5,
    )
    ProjectMembership.objects.create(user=user, project=project)
    dataset = frozen_dataset(project, TRAIN_ROWS, name="ds")
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


def _deploy(project, job, *, model_id=None, status=None) -> DeployedModel:
    deployed = DeployedModel.objects.create(
        finetuning_job=job,
        project=project,
        model_id=model_id or f"ft-{uuid.uuid4().hex[:8]}-qwen2-5-14b",
        status=status or DeployedModel.Status.READY,
        max_model_len=8192,
    )
    job.refresh_from_db()
    return deployed


def _auth_client(user) -> APIClient:
    from rest_framework_simplejwt.tokens import RefreshToken

    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return c


def _get(client, job, *, pin=None):
    url = f"/api/finetuning-jobs/{job.id}/model-swap-prompt/"
    if pin is not None:
        return client.get(url, {"pin": pin})
    return client.get(url)


def test_model_swap_prompt_returns_alias_payload():
    project, user, dataset, capability = _setup()
    job = _make_job(project, dataset, capability=capability)
    deployed = _deploy(project, job, model_id="ft-abc12345-qwen2-5-14b")

    payload, error = model_swap_prompt_for_job(job)
    assert error is None
    alias = f"overmind/{capability.id}"
    assert payload["new_model"] == alias
    assert payload["pin"] is False
    assert alias in payload["prompt"]
    assert deployed.model_id not in payload["prompt"]

    resp = _get(_auth_client(user), job)
    assert resp.status_code == 200
    body = resp.json()
    assert body["prompt"] == payload["prompt"]
    assert body["capability_id"] == str(capability.id)
    assert body["capability_name"] == capability.name
    assert body["new_model"] == alias
    assert body["pin"] is False


def test_model_swap_prompt_pin_writes_concrete_id():
    project, user, dataset, capability = _setup()
    job = _make_job(project, dataset, capability=capability)
    _deploy(project, job, model_id="ft-abc12345-qwen2-5-14b")

    payload, error = model_swap_prompt_for_job(job, pin=True)
    assert error is None
    assert payload["new_model"] == "ft-abc12345-qwen2-5-14b"
    assert payload["pin"] is True
    assert "overmind/" not in payload["prompt"]

    resp = _get(_auth_client(user), job, pin=True)
    assert resp.status_code == 200
    assert resp.json()["new_model"] == "ft-abc12345-qwen2-5-14b"
    assert resp.json()["pin"] is True


def test_model_swap_prompt_rejects_non_succeeded():
    project, user, dataset, capability = _setup()
    job = _make_job(project, dataset, status=FinetuningJob.Status.FAILED, capability=capability)

    resp = _get(_auth_client(user), job)
    assert resp.status_code == 400
    assert "successfully" in resp.json()["detail"].lower()


def test_model_swap_prompt_rejects_missing_model():
    project, user, dataset, capability = _setup()
    job = _make_job(project, dataset, output_model_name="", capability=capability)

    resp = _get(_auth_client(user), job)
    assert resp.status_code == 400
    assert "no deployed model" in resp.json()["detail"]


def test_model_swap_prompt_rejects_unready_deployment():
    project, user, dataset, capability = _setup()
    job = _make_job(project, dataset, capability=capability)
    _deploy(project, job, model_id="ft-warming", status=DeployedModel.Status.DEPLOYING)

    resp = _get(_auth_client(user), job)
    assert resp.status_code == 400
    assert "not ready" in resp.json()["detail"]
    assert "ft-warming" in resp.json()["detail"]


def test_model_swap_prompt_rejects_job_with_no_deployment():
    project, user, dataset, capability = _setup()
    job = _make_job(project, dataset, capability=capability)

    resp = _get(_auth_client(user), job)
    assert resp.status_code == 400
    assert "no deployed model" in resp.json()["detail"]


def test_model_swap_prompt_pin_rejects_unready_deployment():
    project, user, dataset, capability = _setup()
    job = _make_job(project, dataset, capability=capability)
    _deploy(project, job, model_id="ft-warming", status=DeployedModel.Status.DEPLOYING)

    resp = _get(_auth_client(user), job, pin=True)
    assert resp.status_code == 400
    assert "not ready" in resp.json()["detail"]


def test_model_swap_prompt_pin_rejects_job_with_no_deployment():
    project, user, dataset, capability = _setup()
    job = _make_job(project, dataset, capability=capability)

    resp = _get(_auth_client(user), job, pin=True)
    assert resp.status_code == 400
    assert "no deployed model" in resp.json()["detail"]


def test_model_swap_prompt_rejects_ambiguous_unassigned_job():
    project, user, dataset, _ = _setup()
    Capability.objects.create(project=project, name="second-bot", slug="second-bot")
    job = _make_job(project, dataset, capability=None)
    _deploy(project, job)

    resp = _get(_auth_client(user), job)
    assert resp.status_code == 400
    assert "several" in resp.json()["detail"]


def test_model_swap_prompt_falls_back_to_sole_capability():
    project, user, dataset, capability = _setup()
    job = _make_job(project, dataset, capability=None)
    _deploy(project, job)

    resp = _get(_auth_client(user), job)
    assert resp.status_code == 200
    assert resp.json()["capability_id"] == str(capability.id)
    assert resp.json()["new_model"] == f"overmind/{capability.id}"
