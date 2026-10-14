"""A create or start call that carries a request_key runs once per project, tool and key
for 24 hours; repeating it returns the first result."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from overbae.models import MCPRequestKey
from overbae.services.mcp.errors import MCPError

WINDOW = timedelta(hours=24)


def _digest(arguments: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest()


def claim(project, tool: str, key: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """The first result for a repeated key, or None once this call holds the key."""
    digest = _digest(arguments)
    rows = MCPRequestKey.objects.filter(project=project, tool=tool, key=key)
    rows.filter(created_at__lt=timezone.now() - WINDOW).delete()
    try:
        with transaction.atomic():
            MCPRequestKey.objects.create(project=project, tool=tool, key=key, payload_sha256=digest)
        return None
    except IntegrityError:
        row = rows.first()
    if row is not None and row.payload_sha256 != digest:
        raise MCPError(
            "request_key_conflict",
            "This request_key was used with different arguments; use a new key.",
            fields={"request_key": "reused with different arguments"},
        )
    if row is None or row.result is None:
        raise MCPError(
            "request_key_pending",
            "The first call with this request_key has not finished; repeat it shortly.",
            retryable=True,
            retry_after_seconds=5,
        )
    return row.result


def record(project, tool: str, key: str, result: dict[str, Any]) -> None:
    MCPRequestKey.objects.filter(project=project, tool=tool, key=key).update(result=result)


def release(project, tool: str, key: str) -> None:
    MCPRequestKey.objects.filter(project=project, tool=tool, key=key, result__isnull=True).delete()
