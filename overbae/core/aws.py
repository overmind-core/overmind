from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import boto3
from django.conf import settings

_client_factory: Callable[..., Any] | None = None


def register_client_factory(factory: Callable[..., Any] | None) -> None:
    global _client_factory
    _client_factory = factory


def reset_client_factory() -> None:
    global _client_factory
    _client_factory = None


def _resolved_region(region_name: str | None = None) -> str:
    if region_name:
        return region_name
    return getattr(settings, "AWS_REGION", "") or os.environ.get("AWS_REGION", "") or "eu-west-1"


def _credential_kwargs() -> dict[str, str]:
    access_key = (
        getattr(settings, "AWS_ACCESS_KEY_ID", "") or os.environ.get("AWS_ACCESS_KEY_ID", "")
    ).strip()
    secret_key = (
        getattr(settings, "AWS_SECRET_ACCESS_KEY", "")
        or os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    ).strip()
    session_token = os.environ.get("AWS_SESSION_TOKEN", "").strip()
    if access_key and secret_key:
        kwargs = {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
        }
        if session_token:
            kwargs["aws_session_token"] = session_token
        return kwargs
    return {}


def boto3_session(*, region_name: str | None = None) -> boto3.Session:
    region = _resolved_region(region_name)
    profile = os.environ.get("AWS_PROFILE", "").strip()
    if profile:
        return boto3.Session(profile_name=profile, region_name=region)
    return boto3.Session(region_name=region, **_credential_kwargs())


def boto3_client(service_name: str, *, region_name: str | None = None) -> Any:
    region = _resolved_region(region_name)
    if _client_factory is not None:
        return _client_factory(service_name, region_name=region)
    profile = os.environ.get("AWS_PROFILE", "").strip()
    if profile:
        return boto3_session(region_name=region).client(service_name, region_name=region)
    return boto3.client(service_name, region_name=region, **_credential_kwargs())
