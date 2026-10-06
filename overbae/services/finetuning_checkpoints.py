"""Presigned S3 download for a fine-tuning job's archived checkpoint.

Layout written by ``overbae/modal/register_model.py``'s sync_*_to_s3 Functions:
``s3://{bucket}/{user_id}/{finetuning_job_id}/checkpoints/checkpoint.zip``.
"""

from __future__ import annotations

import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

logger = logging.getLogger(__name__)

_PRESIGN_EXPIRES_S = 3600

# A metadata-only archive — config and tokenizer, no weights — is around 20 KB and has been
# written before by an archive that raced the provider's own sync. Retention deletes the last
# volume copy of a checkpoint on the strength of this archive, so anything that small is treated
# as absent. register_model's _zip_has_weights does the exact check; a HEAD cannot see inside.
_MIN_ARCHIVE_BYTES = 1 << 20


class CheckpointArchiveError(Exception):
    pass


def _s3_key(finetuning_job) -> str:
    user_id = str(finetuning_job.triggered_by_id) if finetuning_job.triggered_by_id else "unknown"
    return f"{user_id}/{finetuning_job.id}/checkpoints/checkpoint.zip"


def _s3_client():
    return boto3.client(
        "s3",
        region_name=getattr(settings, "AWS_REGION", "eu-west-1"),
        aws_access_key_id=getattr(settings, "AWS_ACCESS_KEY_ID", "") or None,
        aws_secret_access_key=getattr(settings, "AWS_SECRET_ACCESS_KEY", "") or None,
    )


def checkpoint_archive_exists(finetuning_job) -> bool:
    """Whether the job's weights are durably archived, i.e. safe to prune from the Modal volume.

    Errors answer False: an unreachable bucket must never be read as "archived", because the
    caller deletes the only other copy on the strength of it.
    """
    bucket = getattr(settings, "AWS_BUCKET_NAME", "") or ""
    if not bucket:
        return False

    key = _s3_key(finetuning_job)
    try:
        head = _s3_client().head_object(Bucket=bucket, Key=key)
    except Exception as exc:  # noqa: BLE001
        logger.info("Checkpoint archive not confirmed for %s: %s", key, exc)
        return False
    return (head.get("ContentLength") or 0) >= _MIN_ARCHIVE_BYTES


def get_checkpoint_download_url(finetuning_job) -> dict | None:
    """Absent or unsupported archives return None; storage failures remain errors."""
    if finetuning_job.provider not in ("baseten", "modal"):
        return None
    bucket = getattr(settings, "AWS_BUCKET_NAME", "") or ""
    if not bucket:
        logger.error("Checkpoint downloads are not configured for job %s", finetuning_job.id)
        raise CheckpointArchiveError("Checkpoint downloads are not configured.")

    key = _s3_key(finetuning_job)
    operation = "client initialization"
    try:
        s3 = _s3_client()
        operation = "archive lookup"
        head = s3.head_object(Bucket=bucket, Key=key)
        operation = "link signing"
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=_PRESIGN_EXPIRES_S,
        )
    except (BotoCoreError, ClientError) as exc:
        code = (
            exc.response.get("Error", {}).get("Code", "Unknown")
            if isinstance(exc, ClientError)
            else type(exc).__name__
        )
        if operation == "archive lookup" and code in ("404", "NoSuchKey", "NotFound"):
            logger.info("Checkpoint archive absent for job %s (%s)", finetuning_job.id, code)
            return None
        logger.warning(
            "Checkpoint download failed for job %s during %s (%s)",
            finetuning_job.id,
            operation,
            code,
        )
        detail = (
            "Could not generate a download link."
            if operation == "link signing"
            else "Could not reach the checkpoint archive."
        )
        raise CheckpointArchiveError(detail) from exc

    return {
        "name": "checkpoint.zip",
        "size_bytes": head.get("ContentLength"),
        "download_url": url,
    }
