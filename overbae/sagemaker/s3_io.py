"""S3 sync for the training container. boto3 and the process environment only."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def s3_client():
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "eu-west-1"
    return boto3.client("s3", region_name=region)


def put_bytes(
    bucket: str,
    key: str,
    data: bytes,
    *,
    client=None,
    content_type: str = "application/octet-stream",
) -> None:
    (client or s3_client()).put_object(
        Bucket=bucket,
        Key=key.lstrip("/"),
        Body=data,
        ContentType=content_type,
    )


def put_json(bucket: str, key: str, payload: Any, *, client=None) -> None:
    put_bytes(
        bucket,
        key,
        (json.dumps(payload, indent=2) + "\n").encode("utf-8"),
        client=client,
        content_type="application/json",
    )


def upload_file(bucket: str, key: str, path: Path | str, *, client=None) -> None:
    put_bytes(bucket, key, Path(path).read_bytes(), client=client)


def download_file(bucket: str, key: str, path: Path | str, *, client=None) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    (client or s3_client()).download_file(bucket, key.lstrip("/"), str(target))


def get_bytes(bucket: str, key: str, *, client=None) -> bytes | None:
    try:
        resp = (client or s3_client()).get_object(Bucket=bucket, Key=key.lstrip("/"))
    except (BotoCoreError, ClientError, KeyError):
        return None
    body = resp.get("Body")
    if hasattr(body, "read"):
        return body.read()
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    return None


def get_json(bucket: str, key: str, *, client=None) -> Any | None:
    raw = get_bytes(bucket, key, client=client)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def get_jsonl(bucket: str, key: str, *, client=None) -> list[dict[str, Any]]:
    raw = get_bytes(bucket, key, client=client)
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


def object_exists(bucket: str, key: str, *, client=None) -> bool:
    try:
        (client or s3_client()).head_object(Bucket=bucket, Key=key.lstrip("/"))
        return True
    except (BotoCoreError, ClientError, KeyError):
        return False


def list_keys(bucket: str, prefix: str, *, client=None) -> list[str]:
    s3 = client or s3_client()
    keys: list[str] = []
    token: str | None = None
    try:
        while True:
            kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix.lstrip("/")}
            if token:
                kwargs["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kwargs)
            keys.extend(str(obj["Key"]) for obj in (resp.get("Contents") or []) if "Key" in obj)
            if not resp.get("IsTruncated"):
                return keys
            token = resp.get("NextContinuationToken")
            if not token:
                return keys
    except (BotoCoreError, ClientError):
        return keys


def delete_keys(bucket: str, keys: list[str], *, client=None) -> None:
    s3 = client or s3_client()
    cleaned = [key.lstrip("/") for key in keys if key]
    for start in range(0, len(cleaned), 1000):
        batch = cleaned[start : start + 1000]
        s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
        )


def sync_directory_to_s3(local_dir: Path, bucket: str, prefix: str, *, client=None) -> list[str]:
    uploaded: list[str] = []
    base = prefix.strip("/")
    for file_path in sorted(local_dir.rglob("*")):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(local_dir).as_posix()
        key = f"{base}/{rel}" if base else rel
        upload_file(bucket, key, file_path, client=client)
        uploaded.append(key)
    return uploaded


def sync_s3_to_directory(bucket: str, prefix: str, local_dir: Path, *, client=None) -> list[Path]:
    local_dir.mkdir(parents=True, exist_ok=True)
    base = prefix.strip("/")
    prefix_with_slash = f"{base}/" if base else ""
    downloaded: list[Path] = []
    for key in list_keys(bucket, prefix_with_slash, client=client):
        rel = (
            key[len(prefix_with_slash) :]
            if prefix_with_slash and key.startswith(prefix_with_slash)
            else key
        )
        if not rel or rel.endswith("/"):
            continue
        raw = get_bytes(bucket, key, client=client)
        if raw is None:
            continue
        target = local_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        downloaded.append(target)
    return downloaded
