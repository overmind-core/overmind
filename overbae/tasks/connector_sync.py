"""Celery tasks that pull traces via connector adapters and store them as Spans."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Any

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.db.models import Q
from django.utils import timezone

from overbae.services.connectors.schema import CONNECTOR_VERSION_ATTR

logger = logging.getLogger(__name__)

_LEASE_SECONDS = int(os.environ.get("CONNECTOR_LEASE_SECONDS", "600"))
_BACKOFF_BASE_SECONDS = 30
# Generous enough that a user reading docs mid-setup never loses their draft.
_DRAFT_TTL = timedelta(hours=24)
_BACKOFF_CAP_SECONDS = 3600
_MAX_SYNC_RETRIES = 12
_CHUNK_HARD_TIME_LIMIT = 60 * 20
_CHUNK_SOFT_TIME_LIMIT = 60 * 18


def _match_capabilities_by_name(project, spans: list) -> None:
    """Assign capabilities to unsaved spans through identity.lookup, so renamed
    and merged capabilities still claim their connector spans."""
    from overbae.services.capabilities import identity  # noqa: PLC0415

    cache: dict[str, Any] = {}
    for span in spans:
        if span.capability_id is not None or not span.name:
            continue
        key = span.name.lower()
        if key not in cache:
            cache[key] = identity.lookup(project.id, span.name)
        if cache[key] is not None:
            span.capability = cache[key]


def _stamp_conversations(project, spans: list) -> None:
    """Resolve each span's ``conversation.id`` attribute to a session FK."""
    from overbae.models import Conversation  # noqa: PLC0415

    cache: dict[str, Any] = {}
    for span in spans:
        external_id = (span.attributes or {}).get("conversation.id")
        if not external_id or not isinstance(external_id, str):
            continue
        external_id = external_id[:512]
        if external_id not in cache:
            cache[external_id], _ = Conversation.objects.get_or_create(
                project=project, external_id=external_id
            )
        conversation = cache[external_id]
        span.conversation = conversation
        if conversation.capability_id is None and span.capability_id is not None:
            conversation.capability_id = span.capability_id
            conversation.save(update_fields=["capability"])


def _row_version(span_or_dict) -> int | None:
    """Provider row version from span attributes, or None when the provider is insert-only."""
    attrs = (
        span_or_dict.get("attributes")
        if isinstance(span_or_dict, dict)
        else getattr(span_or_dict, "attributes", None)
    ) or {}
    raw = attrs.get(CONNECTOR_VERSION_ATTR)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


_UPDATE_FIELDS = (
    "trace_id",
    "parent_span_id",
    "span_type",
    "name",
    "start_time_ns",
    "end_time_ns",
    "duration_ns",
    "status_code",
    "status_message",
    "operation",
    "attributes",
    "usage",
    "events",
    "links",
    "capability",
    # Carries the credential's name, so a rename would otherwise stick on every
    # span imported before it.
    "service_name",
)


def _upsert_spans(project, span_dicts: list[dict[str, Any]], credential=None, job=None) -> int:
    """Idempotently insert spans keyed by ``span_id``; returns rows created.

    A provider that rewrites rows in place (Braintrust: async scoring, human
    review) stamps ``connector.version`` and its spans are overwritten when a
    higher version arrives. Rescoring is deliberately not re-enqueued for those —
    a scorer writing back upstream would otherwise loop.
    """
    if not span_dicts:
        return 0
    from overbae.models import Span
    from overbae.models.traces import usage_slice

    existing = {
        span.span_id: span
        for span in Span.objects.filter(span_id__in=[s["span_id"] for s in span_dicts])
    }
    new_spans = []
    updated_spans = []
    for s in span_dicts:
        capability = s.get("capability")
        fields = {k: v for k, v in s.items() if k not in ("span_id", "capability")}
        fields["usage"] = usage_slice(fields.get("attributes"))
        current = existing.get(s["span_id"])
        if current is None:
            new_spans.append(
                Span(project=project, span_id=s["span_id"], capability=capability, **fields)
            )
            continue
        incoming_version = _row_version(s)
        if incoming_version is None or incoming_version <= (_row_version(current) or -1):
            continue
        for key in _UPDATE_FIELDS:
            if key == "capability":
                current.capability = capability
            elif key in fields:
                setattr(current, key, fields[key])
        updated_spans.append(current)

    if updated_spans:
        _match_capabilities_by_name(project, updated_spans)
        _stamp_conversations(project, updated_spans)
        Span.objects.bulk_update(updated_spans, [*_UPDATE_FIELDS, "conversation"])

    if new_spans:
        from overbae.api.otlp import enqueue_trace_scoring

        _match_capabilities_by_name(project, new_spans)
        _stamp_conversations(project, new_spans)
        Span.objects.bulk_create(new_spans, ignore_conflicts=True)
        enqueue_trace_scoring(new_spans)
    return len(new_spans)


def _save_sync_state(credential, **fields) -> None:
    from overbae.models import ConnectorCredential

    ConnectorCredential.objects.filter(pk=credential.pk).update(**fields)
    for key, value in fields.items():
        setattr(credential, key, value)


def _apply_backoff(credential, exc: Exception) -> dict:
    from overbae.models import ConnectorCredential

    retry = credential.sync_retry_count + 1
    status_code = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    auth_failure = status_code in (401, 403)

    if status_code == 429:
        delay = int(getattr(exc, "retry_after", None) or _BACKOFF_BASE_SECONDS)
        _save_sync_state(
            credential,
            sync_status=ConnectorCredential.SyncStatus.ERROR,
            sync_error=str(exc)[:1000],
            next_poll_at=timezone.now() + timedelta(seconds=delay),
        )
        return {"status": "error", "retry_after": delay, "error": str(exc)}

    if auth_failure or retry >= _MAX_SYNC_RETRIES:
        prefix = (
            "Authentication failed — update the credentials, then re-enable auto-sync. "
            if auth_failure
            else "Automatic sync paused after repeated failures — re-enable auto-sync to retry. "
        )
        _save_sync_state(
            credential,
            sync_status=ConnectorCredential.SyncStatus.ERROR,
            sync_error=(prefix + str(exc))[:1000],
            sync_retry_count=retry,
            auto_sync_enabled=False,
            next_poll_at=None,
        )
        logger.warning(
            "connector poll: %s (%s) disabled after %d failures: %s",
            credential.name,
            credential.connector_type,
            retry,
            exc,
        )
        return {"status": "disabled", "error": str(exc)}

    delay = min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** (retry - 1)))
    _save_sync_state(
        credential,
        sync_status=ConnectorCredential.SyncStatus.ERROR,
        sync_error=str(exc)[:1000],
        sync_retry_count=retry,
        next_poll_at=timezone.now() + timedelta(seconds=delay),
    )
    logger.warning(
        "connector poll: %s (%s) backing off %ds after error: %s",
        credential.name,
        credential.connector_type,
        delay,
        exc,
    )
    return {"status": "error", "retry_after": delay, "error": str(exc)}


def _run_adapter_chunk(credential, cursor: dict) -> dict:
    """Provider-blind chunk: adapter.fetch_page → upsert → checkpoint cursor."""
    from overbae.models import ConnectorCredential, ConnectorSyncRun
    from overbae.services.connectors import get_adapter

    adapter = get_adapter(credential)
    page = adapter.fetch_page(dict(cursor))

    config = credential.active_config()
    target_project = (
        config.target_project if config and config.target_project else credential.project
    )
    run_mode = ConnectorSyncRun.Mode.LIVE if page.mode == "live" else ConnectorSyncRun.Mode.BACKFILL

    # Empty backfill completion (no windows left) — flip to LIVE without a run row.
    if page.mode == "backfill" and page.done and not page.units and page.window_from is None:
        _save_sync_state(
            credential,
            sync_status=ConnectorCredential.SyncStatus.LIVE,
            sync_cursor=page.next_state,
            sync_error="",
            sync_retry_count=0,
            last_synced_at=timezone.now(),
            next_poll_at=timezone.now() + timedelta(seconds=credential.poll_interval_seconds),
        )
        return {"status": "backfill_complete", "imported": credential.backfill_imported}

    run = ConnectorSyncRun.objects.create(
        credential=credential,
        config_version=config.version if config else None,
        mode=run_mode,
        window_from=page.window_from,
        window_to=page.window_to,
    )

    imported = 0
    traces_seen = 0
    try:
        for unit in page.units:
            traces_seen += 1
            span_dicts = adapter.to_span_dicts(
                unit,
                credential=credential,
                project=target_project,
            )
            imported += _upsert_spans(target_project, span_dicts, credential=credential)
    except Exception as exc:
        run.status = ConnectorSyncRun.Status.FAILED
        run.error = str(exc)[:1000]
        run.finished_at = timezone.now()
        run.traces_seen = traces_seen
        run.spans_created = imported
        run.save()
        raise

    run.status = ConnectorSyncRun.Status.COMPLETED
    run.finished_at = timezone.now()
    run.traces_seen = traces_seen
    run.spans_created = imported
    run.save()

    credential.refresh_from_db()
    new_traces = credential.total_traces_imported + traces_seen
    type(credential).objects.filter(pk=credential.pk).update(
        total_spans_imported=credential.total_spans_imported + imported,
        total_traces_imported=new_traces,
    )

    if page.done:
        _save_sync_state(
            credential,
            sync_status=ConnectorCredential.SyncStatus.LIVE,
            sync_cursor=page.next_state,
            backfill_imported=new_traces
            if page.mode == "backfill"
            else credential.backfill_imported,
            backfill_total=new_traces if page.mode == "backfill" else credential.backfill_total,
            sync_error="",
            sync_retry_count=0,
            last_synced_at=timezone.now(),
            next_poll_at=timezone.now() + timedelta(seconds=credential.poll_interval_seconds),
        )
        status = "live" if page.mode == "live" else "backfill_complete"
        return {
            "status": status,
            "imported": imported if page.mode == "live" else new_traces,
            "traces": traces_seen,
        }

    _save_sync_state(
        credential,
        sync_status=(
            ConnectorCredential.SyncStatus.LIVE
            if page.mode == "live"
            else ConnectorCredential.SyncStatus.BACKFILLING
        ),
        sync_cursor=page.next_state,
        backfill_imported=(new_traces if page.mode == "backfill" else credential.backfill_imported),
        backfill_total=None if page.mode == "backfill" else credential.backfill_total,
        sync_error="",
        sync_retry_count=0,
        last_synced_at=timezone.now(),
        next_poll_at=None,
    )
    sync_connector_chunk.apply_async(args=[str(credential.id)], countdown=2)
    return {
        "status": "live_continuing" if page.mode == "live" else "backfilling",
        "imported": imported if page.mode == "live" else new_traces,
        "windows_remaining": page.next_state.get("windows_remaining"),
    }


@shared_task(
    name="overbae.tasks.connector_sync.sync_connector_chunk",
    time_limit=_CHUNK_HARD_TIME_LIMIT,
    soft_time_limit=_CHUNK_SOFT_TIME_LIMIT,
)
def sync_connector_chunk(credential_id: str) -> dict:
    """Pull one resumable slice of traces for a connector (backfill or live)."""
    from overbae.models import ConnectorCredential
    from overbae.services.connectors import registered_sources

    credential = ConnectorCredential.objects.filter(id=credential_id, is_active=True).first()
    if not credential:
        return {"status": "skipped", "reason": "credential_missing_or_inactive"}
    if credential.connector_type not in registered_sources():
        return {"status": "skipped", "reason": "unsupported_connector_type"}

    now = timezone.now()
    claimed = (
        ConnectorCredential.objects.filter(pk=credential.pk)
        .filter(Q(next_poll_at__isnull=True) | Q(next_poll_at__lte=now))
        .update(next_poll_at=now + timedelta(seconds=_LEASE_SECONDS))
    )
    if not claimed:
        return {"status": "skipped", "reason": "leased"}
    credential.refresh_from_db()

    cursor = dict(credential.sync_cursor or {})
    if "mode" not in cursor:
        cursor["mode"] = "live" if cursor.get("watermark") else "backfill"

    try:
        return _run_adapter_chunk(credential, cursor)
    except SoftTimeLimitExceeded:
        ConnectorCredential.objects.filter(pk=credential.pk).update(next_poll_at=None)
        sync_connector_chunk.apply_async(args=[str(credential.id)], countdown=2)
        return {"status": "soft_timeout_resumed"}
    except Exception as exc:
        return _apply_backoff(credential, exc)


@shared_task(name="overbae.tasks.connector_sync.sweep_abandoned_drafts")
def sweep_abandoned_drafts() -> dict:
    """Delete wizard drafts whose setup was never finished.

    A draft is created on verify and holds the unique (project, type, name) slot,
    so an orphan blocks re-running setup. The browser-side discard cannot be
    relied on — a closed tab never sends it.
    """
    from overbae.models import ConnectorCredential

    stale = ConnectorCredential.objects.filter(
        configs__isnull=True, created_at__lte=timezone.now() - _DRAFT_TTL
    )
    deleted = 0
    for credential in stale:
        credential.delete()
        deleted += 1
    return {"status": "ok", "deleted": deleted}


@shared_task(name="overbae.tasks.connector_sync.poll_connectors")
def poll_connectors() -> dict:
    """Beat entry point: enqueue a poll chunk for every due active credential."""
    from overbae.models import ConnectorCredential
    from overbae.services.connectors import registered_sources

    now = timezone.now()
    due = ConnectorCredential.objects.filter(is_active=True, auto_sync_enabled=True).filter(
        Q(next_poll_at__isnull=True) | Q(next_poll_at__lte=now)
    )
    sources = registered_sources()
    enqueued = 0
    for credential in due:
        if credential.connector_type not in sources:
            continue
        sync_connector_chunk.apply_async(args=[str(credential.id)])
        enqueued += 1

    return {"status": "ok", "enqueued": enqueued}
