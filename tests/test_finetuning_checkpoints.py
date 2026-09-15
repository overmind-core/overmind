from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from overbae.services.finetuning_checkpoints import (
    CheckpointArchiveError,
    _s3_key,
    get_checkpoint_download_url,
)


def _job(*, provider="modal", user_id="user-1", job_id="job-1"):
    return SimpleNamespace(
        id=job_id,
        provider=provider,
        triggered_by_id=user_id,
    )


def test_s3_key_layout():
    assert _s3_key(_job()) == "user-1/job-1/checkpoints/checkpoint.zip"
    assert _s3_key(_job(user_id=None)) == "unknown/job-1/checkpoints/checkpoint.zip"


def test_get_checkpoint_download_url_requires_bucket(settings):
    settings.AWS_BUCKET_NAME = ""
    with pytest.raises(CheckpointArchiveError, match="not configured"):
        get_checkpoint_download_url(_job())


def test_get_checkpoint_download_url_rejects_unsupported_provider(settings):
    settings.AWS_BUCKET_NAME = "ft-bucket"
    with pytest.raises(CheckpointArchiveError, match="No downloadable weights"):
        get_checkpoint_download_url(_job(provider="together"))


def test_get_checkpoint_download_url_missing_object(settings):
    settings.AWS_BUCKET_NAME = "ft-bucket"
    settings.AWS_REGION = "eu-west-1"
    settings.AWS_ACCESS_KEY_ID = "ak"
    settings.AWS_SECRET_ACCESS_KEY = "sk"

    err = ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
    mock_s3 = MagicMock()
    mock_s3.head_object.side_effect = err

    with (
        patch("boto3.client", return_value=mock_s3),
        pytest.raises(CheckpointArchiveError, match="not ready yet"),
    ):
        get_checkpoint_download_url(_job())


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
