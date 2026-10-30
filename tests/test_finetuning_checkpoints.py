from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from rest_framework.test import APIClient

from overbae.models import Dataset, DeployedModel, FinetuningJob, Project, ProjectMembership, User


def _job(*, provider="modal", user_id="user-1", job_id="job-1"):
    return SimpleNamespace(
        id=job_id,
        provider=provider,
        triggered_by_id=user_id,
    )


@pytest.fixture
def checkpoint_endpoint(db):
    user = User.objects.create_user(email="weights@example.test", password="test")
    project = Project.objects.create(name="Weights", slug="weights")
    ProjectMembership.objects.create(user=user, project=project)
    dataset = Dataset.objects.create(project=project, name="Training")
    job = FinetuningJob.objects.create(
        project=project, dataset=dataset, triggered_by=user, provider="modal", base_model="org/base"
    )
    deployed = DeployedModel.objects.create(
        project=project, finetuning_job=job, model_id="ft-weights", base_model_id="org/base"
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, f"/api/deployed-models/{deployed.id}/checkpoints/", job


@pytest.mark.parametrize("failure", ["access_denied", "transport", "credentials", "signing"])
def test_checkpoint_storage_failures_remain_errors(checkpoint_endpoint, caplog, failure):
    client, url, job = checkpoint_endpoint
    s3 = MagicMock()
    s3.head_object.return_value = {"ContentLength": 2_000_000}
    kwargs = {"return_value": s3}
    if failure == "access_denied":
        s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "private provider detail"}}, "HeadObject"
        )
    elif failure == "transport":
        s3.head_object.side_effect = EndpointConnectionError(endpoint_url="https://s3.example")
    elif failure == "credentials":
        kwargs = {"side_effect": NoCredentialsError()}
    else:
        s3.generate_presigned_url.side_effect = NoCredentialsError()
    with patch("boto3.client", **kwargs):
        response = client.get(url)
    assert response.status_code == 502
    assert "private provider detail" not in response.content.decode()
    assert str(job.id) in caplog.text


def test_checkpoint_listing_rejects_other_project(checkpoint_endpoint):
    client, url, _ = checkpoint_endpoint
    outsider = User.objects.create_user(email="outsider@example.test", password="test")
    client.force_authenticate(user=outsider)
    with patch("boto3.client") as storage:
        response = client.get(url)
    assert response.status_code == 404
    storage.assert_not_called()
