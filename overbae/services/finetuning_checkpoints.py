"""Presigned S3 download for a fine-tuning job's archived checkpoint.

Layout written by ``overbae/modal/register_model.py``'s sync_*_to_s3 Functions:
``s3://{bucket}/{user_id}/{finetuning_job_id}/checkpoints/checkpoint.zip``.
"""

from __future__ import annotations

import logging

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
    import boto3  # noqa: PLC0415
    import botocore.session  # noqa: PLC0415

    # The archive authenticates with the static keys below, but botocore still resolves
    # AWS_PROFILE (set for media storage / Grafana) and raises ProfileNotFound when that
    # profile is absent from the mounted ~/.aws/config. Unbind it for this session.
    core = botocore.session.Session(session_vars={"profile": (None, None, None, None)})
    return boto3.Session(
        botocore_session=core,
        region_name=getattr(settings, "AWS_REGION", "eu-west-1"),
        aws_access_key_id=getattr(settings, "AWS_ACCESS_KEY_ID", "") or None,
        aws_secret_access_key=getattr(settings, "AWS_SECRET_ACCESS_KEY", "") or None,
    ).client("s3")


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


def get_checkpoint_download_url(finetuning_job) -> dict:
    """Return ``{name, size_bytes, download_url}`` for the job's archived checkpoint zip.

    Raises CheckpointArchiveError when the bucket isn't configured or sync_*_to_s3
    has not archived the job yet.
    """
    bucket = getattr(settings, "AWS_BUCKET_NAME", "") or ""
    if not bucket:
        raise CheckpointArchiveError("Checkpoint downloads are not configured.")
    if finetuning_job.provider not in ("baseten", "modal"):
        raise CheckpointArchiveError("No downloadable weights for this fine-tuning job.")

    from botocore.exceptions import BotoCoreError, ClientError  # noqa: PLC0415

    key = _s3_key(finetuning_job)

    try:
        s3 = _s3_client()
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            raise CheckpointArchiveError(
                "Checkpoint archive not ready yet — try again once the model has deployed."
            ) from exc
        logger.warning("S3 head_object failed for %s: %s", key, exc)
        raise CheckpointArchiveError("Could not reach the checkpoint archive.") from exc
    except BotoCoreError as exc:
        logger.warning("S3 client unavailable for %s: %s", key, exc)
        raise CheckpointArchiveError("Could not reach the checkpoint archive.") from exc

    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=_PRESIGN_EXPIRES_S,
        )
    except (ClientError, BotoCoreError) as exc:
        logger.warning("S3 presign failed for %s: %s", key, exc)
        raise CheckpointArchiveError("Could not generate a download link.") from exc

    return {
        "name": "checkpoint.zip",
        "size_bytes": head.get("ContentLength"),
        "download_url": url,
    }
