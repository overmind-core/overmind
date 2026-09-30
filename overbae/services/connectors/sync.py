from __future__ import annotations

from django.db import connection
from rest_framework.exceptions import ValidationError

from overbae.models import ConnectorCredential, Span
from overbae.services.connectors.schema import CONNECTOR_CREDENTIAL_ID_ATTR


def connector_imported_spans(credential):
    cred_id = str(credential.id)
    qs = Span.objects.all()
    if connection.features.supports_json_field_contains:
        return qs.filter(resource_attrs__contains={CONNECTOR_CREDENTIAL_ID_ATTR: cred_id})
    ids = [
        span.span_id
        for span in qs.only("span_id", "resource_attrs")
        if (span.resource_attrs or {}).get(CONNECTOR_CREDENTIAL_ID_ATTR) == cred_id
    ]
    return Span.objects.filter(span_id__in=ids)


def enqueue_connector_sync(credential) -> None:
    from overbae.tasks.connector_sync import (
        sync_connector_chunk,
    )  # worker imports this dispatch service

    if not credential.active_config():
        raise ValidationError("Preview and confirm a range before importing traces.")
    if credential.sync_status == "backfilling":
        return
    ConnectorCredential.objects.filter(pk=credential.pk).update(
        next_poll_at=None, sync_error="", sync_retry_count=0
    )
    sync_connector_chunk.apply_async(args=[str(credential.id)])
