from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from overbae.models import ConnectorCredential, ConnectorImportPreview, ConnectorSyncConfig

PREVIEW_TIMEOUT_SECONDS = 120


def expire_stalled_preview(preview):
    expired = ConnectorImportPreview.objects.filter(
        pk=preview.pk,
        status__in=["queued", "running"],
        created_at__lt=timezone.now() - timedelta(seconds=PREVIEW_TIMEOUT_SECONDS + 60),
    ).update(
        status="failed",
        finished_at=timezone.now(),
        error="Counting did not finish in time. Count the range again or choose a smaller range.",
    )
    if expired:
        preview.refresh_from_db()
    return preview


def request_preview(
    credential, *, source_project_id="", backfill_from=None, backfill_to=None, lookback_days=None
):
    from overbae.tasks.connector_review import (
        preview_connector_import,  # task calls this domain module
    )

    end = backfill_to or timezone.now()
    start = backfill_from or (end - timedelta(days=lookback_days) if lookback_days else None)
    if start and start >= end:
        raise ValidationError("The start of the range must be before its end.")
    if end > timezone.now() + timedelta(minutes=1):
        raise ValidationError("The end of the range cannot be in the future.")
    if not credential.is_active:
        raise ValidationError("Reconnect this integration before importing traces.")
    ConnectorImportPreview.objects.filter(
        credential=credential, status__in=["queued", "running"]
    ).update(
        status="failed", error="A newer count replaced this request.", finished_at=timezone.now()
    )
    preview = ConnectorImportPreview.objects.create(
        credential=credential,
        credential_updated_at=credential.updated_at,
        source_project_id=source_project_id,
        window_from=start,
        window_to=end,
    )
    transaction.on_commit(lambda: preview_connector_import.apply_async(args=[str(preview.id)]))
    return preview


@transaction.atomic
def confirm_import(credential, preview_id):
    from overbae.tasks.connector_sync import (
        sync_connector_chunk,  # worker imports this domain module
    )

    credential = ConnectorCredential.objects.select_for_update().get(pk=credential.pk)
    preview = ConnectorImportPreview.objects.filter(pk=preview_id, credential=credential).first()
    if (
        preview is None
        or preview.status != "ready"
        or not preview.expires_at
        or preview.expires_at <= timezone.now()
    ):
        raise ValidationError("Count this range again before importing traces.")
    if not credential.is_active:
        raise ValidationError("Reconnect this integration before importing traces.")
    if preview.credential_updated_at != credential.updated_at:
        raise ValidationError("The connection changed. Count the range again before importing.")
    if credential.sync_status == "backfilling" or (
        credential.sync_lease_expires_at and credential.sync_lease_expires_at > timezone.now()
    ):
        raise ValidationError("An import is already running for this integration.")
    previous = credential.active_config()
    ConnectorSyncConfig.objects.create(
        credential=credential,
        version=previous.version + 1 if previous else 1,
        source_project_id=preview.source_project_id,
        target_project=credential.project,
        backfill_from=preview.window_from,
        backfill_to=preview.window_to,
        effective_from=timezone.now(),
    )
    ConnectorCredential.objects.filter(pk=credential.pk).update(
        sync_cursor={},
        sync_status="backfilling",
        backfill_imported=0,
        backfill_total=preview.trace_count,
        sync_error="",
        sync_retry_count=0,
        next_poll_at=None,
    )
    preview.status = "imported"
    preview.save(update_fields=["status"])
    transaction.on_commit(lambda: sync_connector_chunk.apply_async(args=[str(credential.id)]))
    return preview


def import_remaining_seconds(credential):
    if credential.sync_status != "backfilling":
        return None
    config = credential.active_config()
    if config is None:
        return None
    elapsed = max(0, (timezone.now() - config.effective_from).total_seconds())
    done, total = credential.backfill_imported, credential.backfill_total
    if total is not None and done >= total:
        return None
    if done and total:
        return max(1, round(elapsed / done * (total - done)))
    preview = credential.previews.filter(
        status="imported",
        source_project_id=config.source_project_id,
        window_from=config.backfill_from,
        window_to=config.backfill_to,
    ).first()
    remaining = preview.estimated_seconds_max - elapsed if preview else 0
    return round(remaining) if remaining >= 1 else None
