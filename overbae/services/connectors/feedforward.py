"""Versioned sync-config creation for connectors."""

from __future__ import annotations

from typing import Any

from django.utils import timezone

_UNSET = object()


def save_sync_config(
    credential,
    *,
    source_project_id: str = "",
    target_project=None,
    lookback_days: int | None = None,
    backfill_from: Any = _UNSET,
    backfill_to: Any = _UNSET,
) -> Any:
    """Create the next config version. Returns the new ConnectorSyncConfig."""
    from overbae.models import ConnectorSyncConfig

    prev = credential.active_config()
    version = (prev.version + 1) if prev else 1
    config = ConnectorSyncConfig.objects.create(
        credential=credential,
        version=version,
        source_project_id=source_project_id or (prev.source_project_id if prev else ""),
        target_project=target_project or (prev.target_project if prev else None),
        lookback_days=lookback_days
        if lookback_days is not None
        else (prev.lookback_days if prev else None),
        backfill_from=prev.backfill_from
        if backfill_from is _UNSET and prev
        else (None if backfill_from is _UNSET else backfill_from),
        backfill_to=prev.backfill_to
        if backfill_to is _UNSET and prev
        else (None if backfill_to is _UNSET else backfill_to),
        effective_from=timezone.now(),
    )

    if prev is None and credential.auto_sync_enabled:
        # First config + auto-sync: kick a normal backfill.
        from overbae.tasks.connector_sync import sync_connector_chunk

        type(credential).objects.filter(pk=credential.pk).update(next_poll_at=None)
        sync_connector_chunk.apply_async(args=[str(credential.id)])

    return config
