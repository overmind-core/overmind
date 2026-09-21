from unittest.mock import Mock

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from overbae.core.errors import InputValidationError
from overbae.models import (
    Cell,
    Dataset,
    DeployedModel,
    FinetuningJob,
    Project,
    ProjectMembership,
    TrainingPreparation,
    User,
)

PRIVATE_DIAGNOSTIC = "Traceback: /srv/private/worker.py provider_token=do-not-expose"


@pytest.fixture
def request_context(db, monkeypatch):
    user = User.objects.create_user(email="errors@test.com", password="pw", clerk_user_id="errors")
    project = Project.objects.create(name="Errors", slug="errors")
    ProjectMembership.objects.create(user=user, project=project)
    dataset = Dataset.objects.create(project=project, name="Training", intent="train")
    cell = Cell.objects.create(dataset=dataset, position=0, state="ok", fingerprint="fixture")
    dataset.active = cell
    dataset.save(update_fields=["active"])
    job = FinetuningJob.objects.create(
        project=project, dataset=dataset, base_model="Qwen/Qwen3-8B", status="failed"
    )
    prep = TrainingPreparation.objects.create(
        cell=cell, signature="fixture", config={}, deadline=timezone.now()
    )
    deployment = DeployedModel.objects.create(
        project=project, finetuning_job=job, model_id="ft-fixture", status="failed"
    )
    client = APIClient()
    client.force_authenticate(user)
    monkeypatch.setattr("overbae.api.credit_gate.require_credits", lambda _: None)
    return client, dataset, job, prep, deployment


@pytest.mark.parametrize("failure_type", [ValueError, RuntimeError, OSError, InputValidationError])
@pytest.mark.parametrize(
    ("surface", "service"),
    [
        ("prepare", "overbae.api.training_preparation.request_preparation"),
        ("retry_preparation", "overbae.api.training_preparation.retry_preparation"),
        ("retry_training", "overbae.api.views.retry_training_preparation"),
        ("recommend", "overbae.services.recommendation.get_recommendation"),
        ("deploy", "overbae.api.views.retry_deployment"),
        ("retry_deployment", "overbae.api.views.retry_deployment"),
    ],
)
def test_api_only_exposes_authored_validation_messages(
    request_context, monkeypatch, caplog, surface, service, failure_type
):
    client, dataset, job, prep, deployment = request_context
    known = failure_type is InputValidationError
    detail = "Choose a supported configuration." if known else PRIVATE_DIAGNOSTIC
    operation = Mock(side_effect=failure_type(detail))
    monkeypatch.setattr(service, operation)
    if surface in {"deploy", "retry_deployment"}:
        job.status = "succeeded"
        job.save(update_fields=["status"])
    routes = {
        "prepare": (
            "/api/training-preparations/",
            {"dataset": str(dataset.id), "model": job.base_model, "context_length": 4096},
        ),
        "retry_preparation": (f"/api/training-preparations/{prep.id}/retry/", {}),
        "retry_training": (f"/api/finetuning-jobs/{job.id}/retry/", {}),
        "recommend": ("/api/finetuning-jobs/recommend/", {"dataset_id": str(dataset.id)}),
        "deploy": (f"/api/deployed-models/{deployment.id}/deploy/", {}),
        "retry_deployment": (f"/api/deployed-models/{deployment.id}/retry/", {}),
    }
    url, body = routes[surface]
    response = client.post(url, body, format="json")
    operation.assert_called_once()
    assert response.status_code == 400, response.data
    assert PRIVATE_DIAGNOSTIC not in response.content.decode()
    if known:
        assert detail in response.content.decode()
    else:
        assert PRIVATE_DIAGNOSTIC in caplog.text
    job.refresh_from_db()
    assert job.status == ("succeeded" if surface in {"deploy", "retry_deployment"} else "failed")
