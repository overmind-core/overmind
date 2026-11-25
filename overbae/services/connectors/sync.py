"""Public connector-sync dispatch shared by API and MCP callers."""

from __future__ import annotations

import json

from django.db.models import Q

from overbae.services.connectors.schema import CONNECTOR_CREDENTIAL_ID_ATTR


def boundary_import_key(mapping: dict | None) -> str:
    payload = mapping if isinstance(mapping, dict) else {}
    return json.dumps(
        {
            "key": payload.get("key") or "",
            "names": sorted(str(name) for name in (payload.get("names") or [])),
            "source": payload.get("source") or "",
        },
        separators=(",", ":"),
    )


def will_recarve(credential) -> bool:
    stored = credential.imported_boundary_key or ""
    if not stored:
        return False
    return stored != boundary_import_key(credential.capability_mapping)


def connector_imported_spans(credential):
    from django.db import connection

    from overbae.models import Span

    cred_id = str(credential.id)
    # Every target it has written to: a changed target must not orphan earlier imports.
    targets = credential.configs.exclude(target_project=None).values("target_project")
    qs = Span.objects.filter(Q(project=credential.project) | Q(project__in=targets))
    if connection.features.supports_json_field_contains:
        return qs.filter(resource_attrs__contains={CONNECTOR_CREDENTIAL_ID_ATTR: cred_id})
    ids = [
        span.span_id
        for span in qs.only("span_id", "resource_attrs")
        if (span.resource_attrs or {}).get(CONNECTOR_CREDENTIAL_ID_ATTR) == cred_id
    ]
    return Span.objects.filter(span_id__in=ids)


def reset_connector_import(credential) -> int:
    """Delete this credential's imported spans and rewind its sync cursor. Only the running
    chunk calls this, so ``next_poll_at``, its lease, stays as it is."""
    from overbae.models import ConnectorCredential

    spans = connector_imported_spans(credential)
    count = spans.count()
    spans.delete()
    ConnectorCredential.objects.filter(pk=credential.pk).update(
        sync_cursor={},
        sync_status=ConnectorCredential.SyncStatus.IDLE,
        total_spans_imported=0,
        total_traces_imported=0,
        backfill_imported=0,
        backfill_total=None,
        sync_error="",
    )
    credential.refresh_from_db()
    return count


# Any stored key other than the live one makes the next chunk recarve under its lease.
SOURCE_CHANGED = "source-changed"


def restart_connector_import(credential, *, source_changed: bool) -> None:
    """After new settings, import the configured range again. Spans from the same source
    project stay, since the import upserts them. Spans from a previous source project are
    removed by the next chunk, so two sources never mix in one project."""
    from overbae.models import ConnectorCredential

    ConnectorCredential.objects.filter(pk=credential.pk).update(
        sync_cursor={},
        sync_status=ConnectorCredential.SyncStatus.IDLE,
        sync_error="",
        next_poll_at=None,
        **({"imported_boundary_key": SOURCE_CHANGED} if source_changed else {}),
    )


def prepare_connector_sync(credential) -> bool:
    """Adopt or recarve so this sync uses the live mapping.names as trace roots.

    An empty stored key is first-seen: adopt without wiping. A changed source or
    names set, or a changed source project, wipes imported spans because incremental
    upsert would keep the old grouping. Assignments-only changes do not recarve.
    """
    from overbae.models import ConnectorCredential

    current = boundary_import_key(credential.capability_mapping)
    stored = credential.imported_boundary_key or ""
    recarving = bool(stored) and stored != current
    if recarving:
        reset_connector_import(credential)
    # A source change saved since the read keeps its marker, so the next chunk recarves.
    if stored != current and ConnectorCredential.objects.filter(
        pk=credential.pk, imported_boundary_key=stored
    ).update(imported_boundary_key=current):
        credential.imported_boundary_key = current
    return recarving


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
    if will_recarve(credential):
        updates["sync_status"] = ConnectorCredential.SyncStatus.BACKFILLING
        updates["sync_cursor"] = {}
    elif cursor.get("mode") != "live":
        updates["sync_status"] = ConnectorCredential.SyncStatus.BACKFILLING
    elif not credential.total_traces_imported:
        updates["sync_status"] = ConnectorCredential.SyncStatus.BACKFILLING
        updates["sync_cursor"] = {}
    ConnectorCredential.objects.filter(pk=credential.pk).update(**updates)
    sync_connector_chunk.apply_async(args=[str(credential.id)])
