from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from overbae.core.aws import boto3_client


def sft_bucket() -> str:
    return (getattr(settings, "AWS_SFT_BUCKET_NAME", "") or "").strip()


def weights_bucket() -> str:
    return (getattr(settings, "AWS_WEIGHTS_BUCKET_NAME", "") or "").strip()


def archive_bucket() -> str:
    return (getattr(settings, "AWS_BUCKET_NAME", "") or "").strip()


def put_bytes(
    bucket: str, key: str, data: bytes, *, content_type: str = "application/octet-stream"
) -> None:
    boto3_client("s3").put_object(
        Bucket=bucket,
        Key=key.lstrip("/"),
        Body=data,
        ContentType=content_type,
    )


def put_json(bucket: str, key: str, payload: Any) -> None:
    put_bytes(
        bucket,
        key,
        (json.dumps(payload, indent=2) + "\n").encode("utf-8"),
        content_type="application/json",
    )


def upload_file(bucket: str, key: str, path: Path | str) -> None:
    put_bytes(bucket, key, Path(path).read_bytes())


def get_bytes(bucket: str, key: str) -> bytes | None:
    try:
        resp = boto3_client("s3").get_object(Bucket=bucket, Key=key.lstrip("/"))
    except (BotoCoreError, ClientError, KeyError):
        return None
    body = resp.get("Body")
    if hasattr(body, "read"):
        return body.read()
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    return None


def get_json(bucket: str, key: str) -> Any | None:
    raw = get_bytes(bucket, key)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def get_jsonl(bucket: str, key: str) -> list[dict[str, Any]]:
    raw = get_bytes(bucket, key)
    if not raw:
        return []
    rows: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
        except ValueError:
            continue
    return rows


def object_exists(bucket: str, key: str) -> bool:
    try:
        boto3_client("s3").head_object(Bucket=bucket, Key=key.lstrip("/"))
        return True
    except (BotoCoreError, ClientError, KeyError):
        return False


def list_keys(bucket: str, prefix: str) -> list[str]:
    try:
        resp = boto3_client("s3").list_objects_v2(Bucket=bucket, Prefix=prefix.lstrip("/"))
    except (BotoCoreError, ClientError):
        return []
    return [str(obj["Key"]) for obj in (resp.get("Contents") or []) if "Key" in obj]


def sync_directory_to_s3(local_dir: Path, bucket: str, prefix: str) -> list[str]:
    uploaded: list[str] = []
    base = prefix.strip("/")
    for file_path in sorted(local_dir.rglob("*")):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(local_dir).as_posix()
        key = f"{base}/{rel}" if base else rel
        upload_file(bucket, key, file_path)
        uploaded.append(key)
    return uploaded


def sync_s3_to_directory(bucket: str, prefix: str, local_dir: Path) -> list[Path]:
    local_dir.mkdir(parents=True, exist_ok=True)
    base = prefix.strip("/")
    prefix_with_slash = f"{base}/" if base else ""
    downloaded: list[Path] = []
    for key in list_keys(bucket, prefix_with_slash):
        rel = (
            key[len(prefix_with_slash) :]
            if prefix_with_slash and key.startswith(prefix_with_slash)
            else key
        )
        if not rel or rel.endswith("/"):
            continue
        raw = get_bytes(bucket, key)
        if raw is None:
            continue
        target = local_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        downloaded.append(target)
    return downloaded
