from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings

from overbae.core.aws import boto3_client
from overbae.sagemaker import s3_io


def _s3():
    return boto3_client("s3")


def sft_bucket() -> str:
    return (getattr(settings, "AWS_SFT_BUCKET_NAME", "") or "").strip()


def weights_bucket() -> str:
    return (getattr(settings, "AWS_WEIGHTS_BUCKET_NAME", "") or "").strip()


def archive_bucket() -> str:
    return (getattr(settings, "AWS_BUCKET_NAME", "") or "").strip()


def put_bytes(
    bucket: str, key: str, data: bytes, *, content_type: str = "application/octet-stream"
) -> None:
    s3_io.put_bytes(bucket, key, data, client=_s3(), content_type=content_type)


def put_json(bucket: str, key: str, payload: Any) -> None:
    s3_io.put_json(bucket, key, payload, client=_s3())


def upload_file(bucket: str, key: str, path: Path | str) -> None:
    s3_io.upload_file(bucket, key, path, client=_s3())


def get_bytes(bucket: str, key: str) -> bytes | None:
    return s3_io.get_bytes(bucket, key, client=_s3())


def get_json(bucket: str, key: str) -> Any | None:
    return s3_io.get_json(bucket, key, client=_s3())


def get_jsonl(bucket: str, key: str) -> list[dict[str, Any]]:
    return s3_io.get_jsonl(bucket, key, client=_s3())


def object_exists(bucket: str, key: str) -> bool:
    return s3_io.object_exists(bucket, key, client=_s3())


def list_keys(bucket: str, prefix: str) -> list[str]:
    return s3_io.list_keys(bucket, prefix, client=_s3())


def delete_run(run_id: str, *, keep_final: bool = False) -> None:
    """Drop one training prefix. ``final/`` stays until deploy has archived it."""
    bucket = sft_bucket()
    run_id = (run_id or "").strip()
    if not bucket or not run_id or "/" in run_id:
        return
    prefix = f"runs/{run_id}/"
    keys = s3_io.list_keys(bucket, prefix, client=_s3())
    if keep_final:
        final = f"{prefix}final/"
        keys = [key for key in keys if not key.startswith(final)]
    s3_io.delete_keys(bucket, keys, client=_s3())


def sync_directory_to_s3(local_dir: Path, bucket: str, prefix: str) -> list[str]:
    return s3_io.sync_directory_to_s3(local_dir, bucket, prefix, client=_s3())


def sync_s3_to_directory(bucket: str, prefix: str, local_dir: Path) -> list[Path]:
    return s3_io.sync_s3_to_directory(bucket, prefix, local_dir, client=_s3())
