"""Public connector-sync dispatch shared by API and MCP callers."""

from __future__ import annotations


def enqueue_connector_sync(credential) -> None:
    """Reset the manual-sync lease and enqueue one connector chunk."""
    from overbae.models import ConnectorCredential
    from overbae.tasks.connector_sync import sync_connector_chunk

    updates = {
        "next_poll_at": None,
        "sync_error": "",
        "sync_retry_count": 0,
    }
    cursor = credential.sync_cursor or {}
    if cursor.get("mode") != "live":
        updates["sync_status"] = ConnectorCredential.SyncStatus.BACKFILLING
    elif not credential.total_traces_imported:
        updates["sync_status"] = ConnectorCredential.SyncStatus.BACKFILLING
        updates["sync_cursor"] = {}
    ConnectorCredential.objects.filter(pk=credential.pk).update(**updates)
    sync_connector_chunk.apply_async(args=[str(credential.id)])
