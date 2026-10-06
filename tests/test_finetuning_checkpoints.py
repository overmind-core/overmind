from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from rest_framework.test import APIClient

from overbae.models import Dataset, DeployedModel, FinetuningJob, Project, ProjectMembership, User
from overbae.services.finetuning_checkpoints import (
    CheckpointArchiveError,
    get_checkpoint_download_url,
)


def _job(*, provider="modal", user_id="user-1", job_id="job-1"):
    return SimpleNamespace(
        id=job_id,
        provider=provider,
        triggered_by_id=user_id,
    )


def test_get_checkpoint_download_url_requires_bucket(settings):
    settings.AWS_BUCKET_NAME = ""
    with pytest.raises(CheckpointArchiveError, match="not configured"):
        get_checkpoint_download_url(_job())


def test_get_checkpoint_download_url_unsupported_provider_has_no_files(settings):
    settings.AWS_BUCKET_NAME = "ft-bucket"
    assert get_checkpoint_download_url(_job(provider="together")) is None


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
def test_get_checkpoint_download_url_missing_object(settings, code):
    settings.AWS_BUCKET_NAME = "ft-bucket"
    settings.AWS_REGION = "eu-west-1"
    settings.AWS_ACCESS_KEY_ID = "ak"
    settings.AWS_SECRET_ACCESS_KEY = "sk"

    err = ClientError({"Error": {"Code": code, "Message": "Not Found"}}, "HeadObject")
    mock_s3 = MagicMock()
    mock_s3.head_object.side_effect = err

    with patch("boto3.client", return_value=mock_s3):
        assert get_checkpoint_download_url(_job()) is None
    mock_s3.generate_presigned_url.assert_not_called()


def test_get_checkpoint_download_url_success(settings):
    settings.AWS_BUCKET_NAME = "ft-bucket"
    settings.AWS_REGION = "eu-west-1"
    settings.AWS_ACCESS_KEY_ID = "ak"
    settings.AWS_SECRET_ACCESS_KEY = "sk"

    mock_s3 = MagicMock()
    mock_s3.head_object.return_value = {"ContentLength": 42}
    mock_s3.generate_presigned_url.return_value = "https://s3.example/checkpoint.zip"

    with patch("boto3.client", return_value=mock_s3):
        result = get_checkpoint_download_url(_job(provider="baseten"))

    assert result == {
        "name": "checkpoint.zip",
        "size_bytes": 42,
        "download_url": "https://s3.example/checkpoint.zip",
    }
    mock_s3.head_object.assert_called_once_with(
        Bucket="ft-bucket",
        Key="user-1/job-1/checkpoints/checkpoint.zip",
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


def test_checkpoint_listing_recovers_when_archive_arrives(checkpoint_endpoint):
    client, url, job = checkpoint_endpoint
    s3 = MagicMock()
    s3.head_object.side_effect = [
        ClientError({"Error": {"Code": "404"}}, "HeadObject"),
        {"ContentLength": 2_000_000},
    ]
    s3.generate_presigned_url.return_value = "https://s3.example/checkpoint.zip"
    with patch("boto3.client", return_value=s3):
        pending = client.get(url)
        assert pending.status_code == 200
        assert pending.json() == {"files": []}
        ready = client.get(url)
    assert ready.status_code == 200
    assert ready.json() == {
        "files": [
            {
                "name": "checkpoint.zip",
                "size_bytes": 2_000_000,
                "download_url": "https://s3.example/checkpoint.zip",
            }
        ]
    }


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


def test_checkpoint_listing_unsupported_provider_skips_storage(checkpoint_endpoint):
    client, url, job = checkpoint_endpoint
    job.provider = "together"
    job.save(update_fields=["provider"])
    with patch("boto3.client") as storage:
        response = client.get(url)
    assert response.status_code == 200
    assert response.json() == {"files": []}
    storage.assert_not_called()


def test_checkpoint_listing_rejects_other_project(checkpoint_endpoint):
    client, url, _ = checkpoint_endpoint
    outsider = User.objects.create_user(email="outsider@example.test", password="test")
    client.force_authenticate(user=outsider)
    with patch("boto3.client") as storage:
        response = client.get(url)
    assert response.status_code == 404
    storage.assert_not_called()


def test_checkpoint_download_configuration_failure_is_logged(checkpoint_endpoint, settings, caplog):
    client, url, job = checkpoint_endpoint
    settings.AWS_BUCKET_NAME = ""
    response = client.get(url)
    assert response.status_code == 502
    assert response.json() == {"detail": "Checkpoint downloads are not configured."}
    assert str(job.id) in caplog.text
